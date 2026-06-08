import atexit
import os
import random
import re
import subprocess
import sys
import unicodedata
from typing import Dict, Optional

from dotenv import load_dotenv
import discord
from discord import app_commands
from discord.ext import commands

from story_data import ATTRIBUTE_LABELS, STORY, determine_final
from storage import (
    create_new_player,
    get_player,
    persist_player,
    load_data,
    save_data,
    load_vale_data,
    persist_vale,
    delete_vale,
    get_vale,
)

from pathlib import Path

LOCK_FILE = Path(__file__).parent / ".bot.lock"

# Vale Sereno (comando /procurar gotas) - estado em memória separado do jogo Gaia
VALE_GAMES: Dict[int, Dict] = {}

# Situações e opções (textos imersivos resumidos aqui; enviados via send_long_message)
VALE_SITUATIONS = [
    {
        "title": "🌧️ SITUAÇÃO 1 — A Ligação",
        "text": (
            "╔══════════════════════════════╗\n"
            "        CAPÍTULO ÚNICO — A LIGAÇÃO\n"
            "╚══════════════════════════════╝\n\n"
            "A chuva começou perto da meia-noite. Não forte, apenas constante — o suficiente para transformar o apartamento em outro lugar.\n\n"
            "O relógio marca 2:13 quando você acorda sem motivo aparente. Não foi sonho. Não foi barulho. Foi sensação. O quarto está escuro, as fendas da janela devolvem luz de rua em tiras.\n\n"
            "O celular vibra na mesa de cabeceira: número desconhecido, nenhum nome. Seu corpo resiste. Ainda assim você atende.\n\n"
            "Silêncio. Depois: respiração. Baixa. Lenta. Próxima demais do microfone. E então, uma voz — a que você jura conhecer, mas que foge quando você tenta alcançá‑la: ‘Você ainda lembra de mim?’\n\n"
            "O aparelho não fala mais. A ligação continua aberta. A energia acaba. A escuridão preenche o cômodo.\n\n"
            "O que você faz?"
        ),
        "options": {
            "A": "Continuar ouvindo",
            "B": "Desligar imediatamente",
            "C": "Quem é você?",
            "D": "O que aconteceu com você?",
        },
    },
    {
        "title": "🌧️ SITUAÇÃO 2 — A Janela",
        "text": (
            "A energia volta alguns segundos depois. Mas o cômodo não é o mesmo: as paredes parecem mais distantes, as sombras esticam. A chuva do lado de fora soa como tecido molhado batendo em metal.\n\n"
            "Alguém está parado na calçada, do outro lado do vidro. Imóvel. Observando. A silhueta é humana, alta o suficiente para cruzar sua vista, e por um instante sua mente insiste que já viu aquele perfil.\n\n"
            "O celular vibra novamente — a ligação continua, mesmo que você tenha desligado. A figura move lentamente a cabeça, como se reconhecesse seu olhar. E algo dentro de você diz que, se ficar olhando, vai lembrar.\n\n"
            "O que você faz?"
        ),
        "options": {
            "A": "Continuar olhando",
            "B": "Fechar a cortina",
            "C": "Sair da casa",
            "D": "Tentar lembrar quem é",
        },
    },
    {
        "title": "🌧️ SITUAÇÃO 3 — O Lago",
        "text": (
            "Você não lembra exatamente como chegou até ali. Em algum momento a chuva e a estrada pareceram puxá‑lo para fora. A neblina se dobra sobre o lago, e a água permanece imóvel, indiferente às gotas.\n\n"
            "Ao se aproximar, percebe que seu reflexo não acompanha seus movimentos. Um atraso mínimo, quase imperceptível — suficiente para que o corpo se enrijeça. No reflexo, alguém está parado atrás de você.\n\n"
            "Você se vira. Não há ninguém. Olha de novo para a água: a figura permanece — agora mais próxima, imóvel, erguendo a mão em sua direção. Ela não tenta sair. Está esperando que você entre.\n\n"
            "O que você faz?"
        ),
        "options": {
            "A": "Perguntar quem é",
            "B": "Tocar a água",
            "C": "Ir embora",
            "D": "Observar o rosto",
        },
    },
]

# Mapeamento choices -> personagens (cada escolha pode sugerir dois personagens)
VALE_CHOICE_MAP = {
    1: {"A": ["Mirela", "Arthur"], "B": ["Vincent", "Noah"], "C": ["Noah", "Arthur"], "D": ["Elena", "Lyra"]},
    2: {"A": ["Mirela", "Lyra"], "B": ["Noah", "Vincent"], "C": ["Vincent", "Arthur"], "D": ["Elena", "Mirela"]},
    3: {"A": ["Elena", "Arthur"], "B": ["Lyra", "Mirela"], "C": ["Noah", "Vincent"], "D": ["Mirela", "Arthur"]},
}

# Características que a cidade observa (atributos ocultos)
VALE_ATTRIBUTES = [
    "apego_emocional",
    "tendencia_obsessiva",
    "medo_da_verdade",
    "sensibilidade_paranormal",
    "instinto_racional",
    "tendencia_autodestrutiva",
    "necessidade_de_controle",
    "atracao_pelo_desconhecido",
]

# Mapear personagens para atributos (incrementos por seleção)
VALE_CHARACTER_TRAITS = {
    "Noah": ["instinto_racional", "necessidade_de_controle", "medo_da_verdade"],
    "Elena": ["apego_emocional", "sensibilidade_paranormal", "tendencia_autodestrutiva"],
    "Vincent": ["medo_da_verdade", "tendencia_autodestrutiva", "tendencia_obsessiva"],
    "Mirela": ["sensibilidade_paranormal", "atracao_pelo_desconhecido", "tendencia_obsessiva"],
    "Arthur": ["tendencia_obsessiva", "necessidade_de_controle", "instinto_racional"],
    "Lyra": ["atracao_pelo_desconhecido", "sensibilidade_paranormal", "apego_emocional"],
}

def _init_vale_state() -> Dict:
    return {
        "stage": 1,
        "choices": [],
        "char_counts": {},
        "attributes": {k: 0 for k in VALE_ATTRIBUTES},
        "awaiting": True,
        "result": None,
    }

def _tally_choice(user_state: Dict, situation_idx: int, choice: str) -> None:
    # count characters
    chars = VALE_CHOICE_MAP.get(situation_idx, {}).get(choice.upper(), [])
    for c in chars:
        user_state["char_counts"][c] = user_state["char_counts"].get(c, 0) + 1
        # increment traits for each character selected
        for trait in VALE_CHARACTER_TRAITS.get(c, []):
            if trait in user_state["attributes"]:
                user_state["attributes"][trait] += 1

def _determine_vale_result(user_state: Dict) -> Dict:
    # choose character with highest count, tiebreaker by predefined order
    counts = user_state.get("char_counts", {})
    order = ["Noah", "Elena", "Vincent", "Mirela", "Arthur", "Lyra"]
    best = None
    best_count = -1
    for c in order:
        if counts.get(c, 0) > best_count:
            best = c
            best_count = counts.get(c, 0)
    # also compute dominant hidden attributes
    attrs = sorted(user_state["attributes"].items(), key=lambda it: it[1], reverse=True)
    dominant_attrs = [a for a, v in attrs if v > 0][:3]
    return {"character": best or "Noah", "dominant_attributes": dominant_attrs, "attributes": user_state["attributes"]}


# Load persisted Vale Sereno sessions into memory on startup
try:
    _saved = load_vale_data()
    for k, v in _saved.items():
        try:
            VALE_GAMES[int(k)] = v
        except Exception:
            VALE_GAMES[k] = v
except Exception:
    VALE_GAMES = {}



def is_process_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}"],
            capture_output=True,
            text=True,
        )
        return str(pid) in result.stdout
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def create_lock_file() -> None:
    if LOCK_FILE.exists():
        try:
            existing_pid = int(LOCK_FILE.read_text().strip())
        except Exception:
            existing_pid = None

        if existing_pid and is_process_running(existing_pid):
            print(f"Outra instância do bot já está rodando (PID {existing_pid}). Saindo.")
            sys.exit(1)
        try:
            LOCK_FILE.unlink()
        except OSError:
            pass

    LOCK_FILE.write_text(str(os.getpid()))


def remove_lock_file() -> None:
    try:
        if LOCK_FILE.exists():
            LOCK_FILE.unlink()
    except OSError:
        pass


atexit.register(remove_lock_file)

load_dotenv(dotenv_path=Path(__file__).parent / ".env")

TOKEN = os.environ.get("DISCORD_TOKEN")
if not TOKEN:
    raise RuntimeError("Defina a variável de ambiente DISCORD_TOKEN antes de executar o bot.")

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix=["/", "&"], intents=intents)

CHOICE_PATTERN = re.compile(r"^[ABCabc][\.)]?$")
BAR_FILLED = "▓"
BAR_EMPTY = "░"

def parse_choice(content: str) -> Optional[str]:
    cleaned = content.strip().upper()
    if cleaned.endswith(")") or cleaned.endswith("."):
        cleaned = cleaned[:-1].strip()
    if cleaned in {"A", "B", "C"}:
        return cleaned
    return None


def normalize_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    normalized = normalized.encode("ASCII", "ignore").decode("ASCII")
    normalized = re.sub(r"[^A-Za-z0-9 ]+", "", normalized)
    normalized = normalized.strip().lower()
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized


def is_valid_answer(answer: str, valid_answers: list[str]) -> bool:
    answer_norm = normalize_text(answer)
    for valid in valid_answers:
        valid_norm = normalize_text(valid)
        if valid_norm == answer_norm or valid_norm in answer_norm:
            return True
    return False

INTRO_TEXT = """`╔══════════════════════════════╗`
                          BEM-VINDA
`╚══════════════════════════════╝`

> Antes de começar…
> 
> eu queria te dizer uma coisa.
> 
> Esse jogo foi feito pensando em você.
> 
> De verdade.
> 
> ---
> 
> Eu me inspirei no pouco que te conheço, nos livros que você disse que gosta —
> na forma como as histórias te prendem,
> na maneira como os personagens sentem de verdade,
> e naquele tipo de narrativa que não é só sobre o que acontece…
> 
> mas sobre o que fica.
> 
> ---
> 
> Aqui, você vai acompanhar a vida da Gaia ao longo de três anos.
> 
> As escolhas que você fizer vão moldar quem ela se torna,
> como ela se relaciona com as pessoas
> e até a forma como ela enxerga o mundo.
> 
> ---
> 
> Não existem escolhas certas.
> 
> Só escolhas que levam a caminhos diferentes.
> 
> Algumas vão aproximar.
> 
> Outras vão afastar.
> 
> Algumas vão fazer sentido só depois."""


INTRO_TEXT_2 = """> ---
> 
> O jogo funciona por capítulos.
> 
> Em certos momentos, você vai precisar escolher entre opções (A, B ou C).
> Cada decisão muda a história — mesmo quando isso não for óbvio.
> 
> ---
> 
> Ao longo da jornada, algumas coisas dentro da Gaia vão mudar.
> 
> Você não vai ver isso diretamente o tempo todo,
> mas pode acompanhar usando o comando **/status**.
> 
> Lá você vai encontrar cinco aspectos:
> 
> **:brain: Razão — como ela pensa, analisa e mantém o controle**
> **:heart: Emoção — como ela se conecta, sente e se abre**
> **:hole: Vazio — o quanto ela evita, se afasta ou se fecha**
> **:mag: Investigação — o quanto ela busca respostas e padrões**
> **:crossed_swords: Ação — o quanto ela age sem hesitar**
> 
> ---
> 
> Esses aspectos mudam com suas escolhas.
> 
> E, no final…
> 
> eles ajudam a definir o caminho que a história vai seguir.
> 
> ---
> 
> Mas não se preocupe com isso agora.
> 
> ---
> 
> Só leia.
> 
> Sinta.
> 
> E escolha como você escolheria.
> 
> ---
> 
> Eu realmente espero que você se divirta com isso…
> e que, de alguma forma, essa história te prenda do jeito que os livros que você gosta prendem você.
> 
> ---
> 
> Agora…
> 
> é só começar.
> 
> Digite /continuar"""

STATE_PHRASES = [
    "Entre o que foi… e o que ainda ecoa.",
    "Algo mudou. Você ainda não sabe o quê.",
    "Nem tudo voltou ao lugar certo.",
    "Há algo no ar que você não consegue nomear.",
    "O silêncio tem peso hoje.",
    "Você sente que algo está por vir.",
]

TREND_PHRASES = {
    "razao": "Você está analisando tudo… até o que não deveria.",
    "emocao": "Você ainda sente mais do que entende.",
    "vazio": "Às vezes, não agir parece mais natural do que escolher.",
    "investigacao": "A verdade está próxima… ou você está indo longe demais.",
    "acao": "Você resolve antes de pensar… e isso tem funcionado.",
}

RECENT_PHRASES = [
    "Você hesitou… e isso ainda permanece.",
    "Você escolheu agir sem pensar duas vezes.",
    "Você ignorou algo que talvez fosse importante.",
    "Você percebeu algo que outros não perceberiam.",
    "Você deixou algo te afetar mais do que deveria.",
    "Você decidiu seguir em frente, mesmo com dúvidas.",
]

STRANGE_PHRASES = {
    "razao": "Você tem certeza de que isso aconteceu assim?",
    "emocao": "Você sente isso… ou só quer sentir?",
    "vazio": "Há um vazio que você não consegue preencher.",
    "investigacao": "Você está perto demais da verdade.",
    "acao": "Você agiu. Mas foi sua escolha?",
}


@bot.event
async def on_ready() -> None:
    print(f"Bot conectado como {bot.user}")
    try:
        synced = await bot.tree.sync()
        print(f"Slash commands sincronizados: {len(synced)}")
    except Exception as exc:
        print("Falha ao sincronizar comandos:", exc)


def get_block(chapter: int, block: int) -> Optional[Dict]:
    return STORY.get(chapter, {}).get(block)


def format_block(block: Dict) -> str:
    text = block["text"]
    if "choices" in block:
        text += "\n\n" + "Escolhas:\n"
        for label, content in block["choices"].items():
            text += f"{label}: {content['text']}\n"
    return text


def update_attributes(attributes: Dict[str, int], points: Dict[str, int]) -> None:
    for key, value in points.items():
        attributes[key] = attributes.get(key, 0) + value


def get_status_text(attributes: Dict[str, int], choices: list) -> str:
    import random
    
    # Get dominant attribute
    if not attributes or sum(attributes.values()) == 0:
        state_phrase = "A jornada ainda não começou… ou mal começou."
    else:
        state_phrase = random.choice(STATE_PHRASES)
    
    # Build bars
    bars_lines = []
    for key, label in ATTRIBUTE_LABELS.items():
        value = min(attributes.get(key, 0), 10)
        filled = BAR_FILLED * value
        empty = BAR_EMPTY * (10 - value)
        
        # Check for strange effects
        is_strange = (key in ["investigacao", "vazio"]) and value >= 7
        value_str = f"({value})" if not is_strange else f"({value}?)"
        
        emoji = {"razao": "🧠", "emocao": "❤️", "vazio": "🕳️", "investigacao": "🔍", "acao": "⚔️"}.get(key, "○")
        bars_lines.append(f"{emoji} {label:15} {filled}{empty} {value_str}")
    
    bars_text = "\n".join(bars_lines)
    
    # Get dominant for trend
    if attributes and sum(attributes.values()) > 0:
        dominant = max(attributes.items(), key=lambda item: item[1])[0]
        is_strange_trend = attributes.get(dominant, 0) >= 7 and dominant in ["investigacao", "vazio"]
        trend_phrase = STRANGE_PHRASES.get(dominant, TREND_PHRASES.get(dominant, "")) if is_strange_trend else TREND_PHRASES.get(dominant, "O caminho ainda se define.")
    else:
        trend_phrase = "A jornada está apenas começando."
    
    # Get recent choice for record
    if choices:
        last_choice = choices[-1]
        record_phrase = random.choice(RECENT_PHRASES)
    else:
        record_phrase = "Nenhuma escolha feita ainda."
    
    return f"""╔══════════════════════════════╗
        ◈ STATUS DE GAIA ◈
╚══════════════════════════════╝

▸ Estado atual:
"{state_phrase}"

━━━━━━━━━━━━━━━━━━━━━━

{bars_text}

━━━━━━━━━━━━━━━━━━━━━━

◈ Tendência atual:
"{trend_phrase}"

━━━━━━━━━━━━━━━━━━━━━━

◈ Registro recente:
"{record_phrase}"

━━━━━━━━━━━━━━━━━━━━━━

Digite /continuar"""


def advance_to_next_block(player_data: Dict[str, any]) -> None:
    chapter = player_data["chapter"]
    block = player_data["block"]
    current = get_block(chapter, block)
    if not current:
        return
    next_value = current.get("next")
    if next_value is None:
        if chapter < 5:
            player_data["chapter"] = chapter + 1
            player_data["block"] = 1
        else:
            player_data["block"] = block
        return
    if isinstance(next_value, (list, tuple)) and len(next_value) == 2:
        player_data["chapter"], player_data["block"] = next_value
    else:
        player_data["block"] = next_value


END_CHAPTER_ORDER = ["investigacao", "vazio", "emocao", "razao", "acao"]
END_CHAPTER_PHRASES = {
    "razao": [
        "Nem tudo precisa ser sentido… desde que possa ser entendido.",
        "Você escolhe manter o controle, mesmo quando algo tenta tirá-lo.",
        "Se há um padrão, você vai encontrar — antes que ele encontre você.",
    ],
    "emocao": [
        "Algumas respostas não estão na lógica… mas em quem você escolhe confiar.",
        "Você sente antes de entender — e, dessa vez, isso importa.",
        "Nem tudo que te puxa é erro. Às vezes… é direção.",
    ],
    "vazio": [
        "Ignorar também é uma escolha… e ela sempre cobra depois.",
        "O silêncio pode esconder… mas nunca apaga.",
        "Você escolhe não olhar — mas algo continua olhando por você.",
    ],
    "investigacao": [
        "Você não deixa perguntas existirem sem resposta.",
        "Se algo começou… você vai até o fim.",
        "Quanto mais você vê, menos consegue parar.",
    ],
    "acao": [
        "Pensar pode esperar. Você já escolheu agir.",
        "Você não precisa entender tudo para começar.",
        "O primeiro passo já foi dado… mesmo sem saber para onde.",
    ],
}

def get_dominant_attribute(attributes: Dict[str, int]) -> str:
    dominant = None
    for attr in END_CHAPTER_ORDER:
        if dominant is None or attributes.get(attr, 0) > attributes.get(dominant, 0):
            dominant = attr
    return dominant or "razao"


def distort_phrase(phrase: str) -> str:
    if phrase.endswith("..."):
        return f"{phrase} certo?"
    if phrase.endswith("."):
        return f"{phrase[:-1]}... certo?"
    return f"{phrase}... certo?"


def get_chapter_end_phrase(attributes: Dict[str, int]) -> str:
    dominant = get_dominant_attribute(attributes)
    phrase = random.choice(END_CHAPTER_PHRASES.get(dominant, []))
    if attributes.get("investigacao", 0) >= 7 or attributes.get("vazio", 0) >= 7:
        phrase = distort_phrase(phrase)
    return phrase


def get_chapter_1_end_text(attributes: Dict[str, int]) -> str:
    phrase = get_chapter_end_phrase(attributes)
    return f"━━━━━━━━━━━━━━━━━━━━━━\n“{phrase}”\n━━━━━━━━━━━━━━━━━━━━━━\n1/5"


def get_final_result(player_data: Dict[str, any]) -> str:
    final = determine_final(player_data["attributes"])
    return f"O final de Gaia é: **{final}**.\n" + (
        "A história conclui que, no fim, cada escolha desenhou o destino que ela merecia."
    )


async def send_current_block(user_id: int, respond_func, player_data: Dict[str, any], should_persist: bool = True) -> None:
    if player_data.get("finished"):
        await respond_func("A jornada de Gaia já terminou. Use /reiniciar para começar de novo.")
        return

    chapter = player_data["chapter"]
    block = player_data["block"]
    current = get_block(chapter, block)
    if not current:
        await send_long_message(respond_func, "A jornada de Gaia já chegou ao fim. Use /reiniciar para começar de novo.")
        player_data["finished"] = True
        player_data["awaiting_choice"] = False
        if should_persist:
            persist_player(user_id, player_data)
        return

    text = f"**Capítulo {chapter} • Bloco {block}**\n\n{current['text']}"
    if "choices" in current:
        text += "\n\nResponda com A, B ou C para seguir."
        player_data["awaiting_choice"] = True
        if should_persist:
            persist_player(user_id, player_data)
        await send_long_message(respond_func, text)
        return

    if current.get("requires_answer"):
        text += "\n\nResponda com a frase correta para continuar."
        player_data["awaiting_choice"] = True
        if should_persist:
            persist_player(user_id, player_data)
        await send_long_message(respond_func, text)
        return

    await send_long_message(respond_func, text)
    if current.get("next") is not None and "/continuar" not in current["text"].lower():
        await send_long_message(respond_func, "Use /continuar para seguir a história.")
    else:
        if current.get("next") is None:
            if chapter == 5:
                result_text = get_final_result(player_data)
                await send_long_message(respond_func, result_text)
                player_data["finished"] = True
                player_data["awaiting_choice"] = False
                if should_persist:
                    persist_player(user_id, player_data)
                return
            if STORY.get(chapter + 1):
                await send_long_message(respond_func, "Capítulo concluído. Use `/continuar` para seguir para o próximo capítulo.")
            else:
                await send_long_message(respond_func, "A jornada de Gaia chegou ao fim. Obrigado por jogar!")
                player_data["finished"] = True
                player_data["awaiting_choice"] = False
                if should_persist:
                    persist_player(user_id, player_data)
                return

    player_data["awaiting_choice"] = False
    if should_persist:
        persist_player(user_id, player_data)


def textwrap(text: str) -> str:
    return text


MAX_DISCORD_MESSAGE_LENGTH = 2000

def split_message(text: str, limit: int = MAX_DISCORD_MESSAGE_LENGTH) -> list[str]:
    chunks = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break
        split_at = text.rfind("\n", 0, limit)
        if split_at == -1:
            split_at = text.rfind(" ", 0, limit)
        if split_at == -1:
            split_at = limit
        chunks.append(text[:split_at].rstrip())
        text = text[split_at:].lstrip()
    return chunks


async def send_long_message(respond_func, text: str) -> None:
    for chunk in split_message(text):
        await respond_func(chunk)


class InteractionResponder:
    def __init__(self, interaction: discord.Interaction):
        self.interaction = interaction
        self.responded = False

    async def send(self, content: str) -> None:
        if not self.responded:
            await self.interaction.response.send_message(content)
            self.responded = True
        else:
            await self.interaction.followup.send(content)


async def handle_iniciar(send_func, user_id: int, nome: str = None) -> None:
    player_data = get_player(user_id)
    if player_data:
        if player_data.get("finished"):
            await send_func("Sua jornada anterior terminou. Use /reiniciar para começar de novo.")
            return
        await send_func("Você já iniciou a jornada. Use /continuar para seguir de onde parou.")
        return
    if nome and nome.lower() != "gaia":
        await send_func("Eu disse… **Gaia**.")
        return
    await send_func(INTRO_TEXT)
    await send_func(INTRO_TEXT_2)


async def handle_continuar(send_func, user_id: int) -> None:
    player_data = get_player(user_id)
    if player_data and player_data.get("finished"):
        await send_func("A jornada de Gaia já terminou. Use /reiniciar para começar de novo.")
        return
    if not player_data:
        # New player: create and show the first block instead of auto-advancing
        player_data = create_new_player()
        persist_player(user_id, player_data)
        await send_current_block(user_id, send_func, player_data)
        return

    if player_data.get("awaiting_choice"):
        current = get_block(player_data["chapter"], player_data["block"])
        if current and current.get("requires_answer"):
            await send_func("Uma resposta está pendente. Responda com o texto correto para continuar.")
            return
        await send_func("Uma escolha está pendente. Responda com A, B ou C para continuar.")
        return
    # If the current block requires an answer, prompt for it
    current = get_block(player_data["chapter"], player_data["block"])
    if not current:
        await send_func("A jornada de Gaia já chegou ao fim. Use /reiniciar para começar de novo.")
        player_data["finished"] = True
        persist_player(user_id, player_data)
        return

    if current.get("requires_answer"):
        await send_func("Uma resposta está pendente. Responda com o texto correto para continuar.")
        return

    # If this block has a next pointer, /continuar should advance to it
    if current.get("next") is not None:
        advance_to_next_block(player_data)
        persist_player(user_id, player_data)
        await send_current_block(user_id, send_func, player_data)
        return

    # If there's no explicit next, treat as end-of-chapter or end-of-story
    chapter = player_data["chapter"]
    if chapter == 5:
        result_text = get_final_result(player_data)
        await send_func(result_text)
        player_data["finished"] = True
        persist_player(user_id, player_data)
        return
    # advance to next chapter start
    if STORY.get(chapter + 1):
        advance_to_next_block(player_data)
        persist_player(user_id, player_data)
        await send_current_block(user_id, send_func, player_data)
        return

    await send_func("A jornada de Gaia chegou ao fim. Obrigado por jogar!")
    player_data["finished"] = True
    persist_player(user_id, player_data)
    return


async def handle_status(send_func, user_id: int) -> None:
    player_data = get_player(user_id)
    if not player_data:
        await send_func("Você ainda não iniciou a jornada. Use `/iniciar Gaia` para começar.")
        return
    status_text = get_status_text(player_data["attributes"], player_data["choices"])
    await send_func(status_text)

def reset_player_to_chapter(user_id: int, chapter: int, block: int = 1) -> Dict[str, any]:
    player_data = create_new_player()
    player_data["chapter"] = chapter
    player_data["block"] = block
    persist_player(user_id, player_data)
    return player_data


async def handle_choose_chapter(send_func, user_id: int, chapter: int, block: Optional[int] = None) -> None:
    if chapter not in STORY:
        chapters = sorted(STORY.keys())
        await send_func(
            f"Capítulo {chapter} não existe. Capítulos disponíveis: {', '.join(str(ch) for ch in chapters)}."
        )
        return
    if block is None:
        if chapter == 2:
            await send_func(
                "O Capítulo 2 tem múltiplos pontos de entrada. Use `/capitulo 2 1`, `/capitulo 2 2` ou `/capitulo 2 3` para começar no bloco desejado."
            )
            return
        block = 1
    if block not in STORY[chapter]:
        available = sorted(STORY[chapter].keys())
        await send_func(
            f"Bloco {block} não existe no Capítulo {chapter}. Blocos disponíveis: {', '.join(str(b) for b in available)}."
        )
        return
    reset_player_to_chapter(user_id, chapter, block)
    await send_func(
        f"Progresso reiniciado para o Capítulo {chapter} no Bloco {block}. Use `/continuar` para começar deste ponto."
    )

async def handle_reiniciar(send_func, user_id: int) -> None:
    player_data = get_player(user_id)
    if not player_data:
        await send_func("Você ainda não iniciou nenhuma jornada. Use `/iniciar Gaia` para começar.")
        return
    data = load_data()
    if str(user_id) in data:
        del data[str(user_id)]
        save_data(data)
    await send_func("Sua jornada foi reiniciada. Use `/iniciar Gaia` para começar novamente.")


def get_vale_profile_text(result: Dict[str, any]) -> str:
    character = result.get("character", "Desconhecido")
    profiles = {
        "Noah": (
            "🌧️ NOAH VESPER\n"
            "’O que tenta racionalizar o impossível’\n\n"
            "Noah Vesper nunca acreditou em fantasmas.\n\n"
            "Mesmo depois do acidente.\n\n"
            "Mesmo depois da fotografia.\n\n"
            "Mesmo depois da irmã morta aparecer sorrindo no fundo de uma imagem tirada três anos após o enterro dela.\n\n"
            "Ele ainda preferia acreditar em erro.\n\n"
            "Manipulação.\n"
            "Coincidência.\n"
            "Fraude.\n"
            "Qualquer coisa que pudesse ser desmontada com lógica.\n\n"
            "Talvez fosse exatamente por isso que Noah se tornou jornalista investigativo. Não porque gostava da verdade — mas porque precisava desesperadamente acreditar que ela existia. Durante anos, construiu uma carreira destruindo histórias sobrenaturais, cultos, lendas urbanas e desaparecimentos ‘paranormais’. Sempre existia uma explicação escondida atrás do horror. Sempre existia alguém mentindo.\n\n"
            "Até Vale Sereno.\n\n"
            "A fotografia chegou dentro de um envelope sem remetente numa terça-feira chuvosa. Ele lembrava disso porque a chuva nunca mais pareceu normal depois daquele dia. A imagem mostrava uma rua antiga, aparentemente vazia, tirada na década de noventa. A data estava marcada no canto inferior.\n\n"
            "Três anos após a morte da irmã dele.\n\n"
            "E ainda assim—\n"
            "ela estava lá.\n\n"
            "No reflexo de uma vitrine ao fundo da fotografia.\n\n"
            "Observando a câmera.\n\n"
            "Noah passou semanas tentando racionalizar aquilo. Analisou a imagem dezenas de vezes, pesquisou manipulação digital, procurou registros da cidade, tentou encontrar qualquer evidência concreta de falsificação. Mas quanto mais investigava, mais coisas erradas encontrava.\n\n"
            "Vale Sereno praticamente não existia.\n\n"
            "Os registros da cidade eram incompletos. Algumas fotografias antigas mudavam dependendo de onde eram vistas. Certos moradores apareciam mortos em um documento e vivos em outro. Ruas inteiras desapareciam dos mapas oficiais dependendo do ano pesquisado.\n\n"
            "E o pior:\n"
            "sempre que Noah tentava abandonar o caso, alguma coisa acontecia.\n\n"
            "O rádio do carro ligava sozinho na mesma estação.\n"
            "A chuva começava sem previsão.\n"
            "O relógio parava exatamente às 2:13.\n\n"
            "Ele começou a dormir menos depois disso. Não por medo. Noah não gostava da palavra medo. Ele dizia para si mesmo que era obsessão profissional. Curiosidade. Necessidade de concluir o caso.\n\n"
            "Mas, no fundo, existia outra coisa.\n\n"
            "A possibilidade absurda de que a irmã realmente estivesse esperando ele naquela cidade.\n\n"
            "Noah começou a perceber pequenos detalhes estranhos na própria rotina. Reflexos demorando para acompanhar movimentos. Pessoas aparentemente reconhecendo ele em lugares onde nunca esteve. E sonhos.\n\n"
            "Sempre os mesmos sonhos.\n\n"
            "Uma rua coberta por chuva.\n"
            "Uma mulher parada atrás de uma janela.\n"
            "E um lago onde o reflexo nunca olhava de volta da forma correta.\n\n"
            "Ele nunca contou isso para ninguém.\n\n"
            "Porque Noah não tem medo do sobrenatural.\n\n"
            "Ele tem medo de descobrir que passou a vida inteira tentando destruir coisas que eram reais.\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "PERSONALIDADE\n"
            "extremamente inteligente\n"
            "racional\n"
            "observador\n"
            "emocionalmente reprimido\n"
            "sarcástico quando desconfortável\n"
            "tenta controlar tudo ao redor\n"
            "péssimo em lidar com perda emocional\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "O QUE VALE SERENO ENXERGA NELE\n"
            "Noah acredita que busca respostas.\n\n"
            "Mas a cidade percebe outra coisa:\n\n"
            "Ele quer provar que ainda existe lógica no mundo.\n\n"
            "E Vale Sereno adora pessoas que têm algo importante para perder.\n\n"
            "’Existem verdades que enlouquecem menos quando permanecem enterradas.’"
        ),
        "Elena": (
            "🌧️ ELENA MOURA\n"
            "’A que continua procurando alguém’\n\n"
            "Elena nunca conseguiu aceitar silêncio.\n\n"
            "Depois do acidente, foi isso que mais destruiu ela.\n\n"
            "Não a ausência do filho.\n"
            "Não o enterro.\n"
            "Nem mesmo a sensação constante de culpa.\n\n"
            "Foi o silêncio.\n\n"
            "A casa deixou de ter som depois que Miguel morreu. Os brinquedos permaneceram no mesmo lugar por meses, como se qualquer alteração significasse admitir que ele não voltaria. Elena dizia para si mesma que ainda precisava de tempo, mas o tempo nunca pareceu interessado em ajudar.\n\n"
            "Ela começou a trabalhar mais depois disso. Plantões longos, noites inteiras acordada no hospital, qualquer coisa que impedisse ela de voltar cedo para casa. Porque, toda vez que entrava no apartamento vazio, tinha a sensação absurda de que alguma coisa ainda estava esperando por ela ali.\n\n"
            "Às vezes, jurava ouvir passos pequenos no corredor.\n\n"
            "Outras vezes, encontrava desenhos infantis em lugares onde tinha certeza que havia guardado tudo.\n\n"
            "Ela nunca contou isso para ninguém.\n\n"
            "Porque uma mãe enlutada ouvindo o filho morto parecia exatamente o tipo de história triste que as pessoas comentariam em voz baixa pelos corredores do hospital.\n\n"
            "Então a ligação aconteceu.\n\n"
            "2:13 da manhã.\n"
            "Número desconhecido.\n"
            "Chiado.\n"
            "Chuva.\n\n"
            "E uma voz infantil dizendo:\n\n"
            "’Mãe?’\n\n"
            "Elena deixou o celular cair naquele instante.\n\n"
            "Mas o pior não foi isso.\n\n"
            "O pior foi reconhecer a voz.\n\n"
            "Miguel tinha uma forma específica de pronunciar certas palavras. Pequenos erros na fala que desapareceriam conforme crescesse. E aquela voz… ainda falava exatamente igual.\n\n"
            "Ela passou dias tentando convencer a si mesma de que aquilo era impossível. Tentou apagar a ligação. Trocar de número. Ignorar.\n\n"
            "Mas então começaram os sonhos.\n\n"
            "Uma cidade coberta por chuva.\n"
            "Postes piscando.\n"
            "Uma igreja tocando sinos na madrugada.\n"
            "E Miguel parado do outro lado da rua, sempre distante demais para alcançar.\n\n"
            "Ela acordava chorando sem perceber.\n\n"
            "E, aos poucos, começou a desejar dormir mais do que ficar acordada.\n\n"
            "Porque os sonhos eram o único lugar onde ele ainda existia.\n\n"
            "Quando a carta de Vale Sereno chegou, Elena já sabia o que encontraria dentro antes mesmo de abrir.\n\n"
            "’Se você ainda procura alguém…\n"
            "venha.’\n\n"
            "Ela nunca contou isso para ninguém.\n\n"
            "Nem o fato de que, no fundo…\n"
            "uma parte dela queria desesperadamente que tudo fosse real.\n\n"
            "Mesmo que fosse errado.\n"
            "Mesmo que fosse monstruoso.\n"
            "Mesmo que aquela cidade estivesse apenas usando a dor dela.\n\n"
            "Porque mães continuam procurando os filhos mesmo quando o mundo inteiro manda parar.\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "PERSONALIDADE\n"
            "extremamente empática\n"
            "gentil\n"
            "emocional\n"
            "protetora\n"
            "sensível ao sofrimento alheio\n"
            "dificuldade absurda em abandonar pessoas\n"
            "carrega culpa constantemente\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "O QUE VALE SERENO ENXERGA NELA\n"
            "A cidade não vê Elena como alguém fraca.\n"
            "Ela vê alguém perigosa.\n\n"
            "Porque pessoas movidas por amor atravessam lugares que pessoas normais jamais atravessariam.\n\n"
            "’Algumas pessoas não sobrevivem à perda.\n"
            "Algumas continuam vivendo dentro dela.’"
        ),
        "Vincent": (
            "🌧️ VINCENT HALE\n"
            "’O que fugiu do próprio passado’\n\n"
            "Vincent Hale passou a vida inteira aprendendo a controlar reações.\n\n"
            "Respiração.\n"
            "Postura.\n"
            "Expressão.\n"
            "Tom de voz.\n\n"
            "Como policial, isso era necessário. Pessoas desesperadas procuram estabilidade no rosto de alguém armado. E Vincent era bom nisso. Sempre foi. O tipo de homem que entrava em uma sala caótica e fazia tudo parecer sob controle apenas ficando em silêncio.\n\n"
            "Até aquela noite.\n\n"
            "O relatório oficial dizia:\n\n"
            "’Suspeito armado neutralizado durante abordagem.’\n\n"
            "Simples.\n"
            "Objetivo.\n"
            "Aceitável.\n\n"
            "Mas Vincent lembra de outra coisa.\n\n"
            "A chuva.\n\n"
            "Ela caía exatamente igual à de Vale Sereno.\n\n"
            "O homem que morreu naquela noite não estava armado. Vincent percebeu isso tarde demais. Um segundo depois do disparo. Tempo suficiente para destruir uma vida inteira e curto demais para desfazer qualquer coisa.\n\n"
            "O resto aconteceu rápido.\n\n"
            "Os superiores abafaram o caso.\n"
            "As testemunhas desapareceram.\n"
            "Os documentos mudaram.\n"
            "O corpo foi enterrado antes da família conseguir recorrer.\n\n"
            "E Vincent deixou acontecer.\n\n"
            "É isso que destrói ele.\n\n"
            "Não o tiro.\n\n"
            "A omissão.\n\n"
            "Depois disso, começou a beber mais. Dormir menos. Se afastar lentamente das pessoas ao redor. O apartamento virou um lugar silencioso demais. As noites ficaram longas demais. E toda vez que a chuva começava…\n\n"
            "alguma coisa voltava.\n\n"
            "No início eram sonhos.\n\n"
            "Depois reflexos.\n\n"
            "Às vezes ele via o homem parado atrás dele em espelhos. Nunca agressivo. Nunca sangrando. Apenas observando.\n\n"
            "Como se estivesse esperando Vincent admitir alguma coisa.\n\n"
            "Ele nunca admitiu.\n\n"
            "Até a carta chegar.\n\n"
            "Envelope escuro.\n"
            "Sem remetente.\n\n"
            "Dentro, apenas uma frase:\n\n"
            "’Eu ainda estou esperando.’\n\n"
            "Assinada pelo homem que ele matou.\n\n"
            "Vincent queimou a carta imediatamente.\n\n"
            "Ou tentou.\n\n"
            "O papel não pegou fogo.\n\n"
            "Na mesma noite, o rádio do apartamento ligou sozinho às 2:13 da manhã. Chiado. Chuva. E então:\n\n"
            "’Você devia ter olhado direito.’\n\n"
            "Vincent arrancou o rádio da tomada.\n\n"
            "A voz continuou.\n\n"
            "Depois disso, ele começou a perceber coisas impossíveis. Pessoas olhando tempo demais para ele na rua. Sirenes tocando sem origem. O som de passos molhados atrás dele em corredores vazios.\n\n"
            "Mas o pior era a sensação constante de que alguma coisa estava aproximando ele lentamente de um lugar específico.\n\n"
            "Como se todos os caminhos terminassem no mesmo ponto.\n\n"
            "Vale Sereno.\n\n"
            "Ele pesquisou a cidade obsessivamente nas semanas seguintes. Encontrou desaparecimentos antigos, fotos inconsistentes, acidentes sem explicação e relatos apagados da internet poucas horas após serem publicados.\n\n"
            "E então encontrou o lago.\n\n"
            "Não pessoalmente.\n\n"
            "Em sonho.\n\n"
            "Sempre o mesmo sonho.\n\n"
            "A água parada.\n\n"
            "A chuva.\n\n"
            "E o homem esperando do outro lado.\n\n"
            "Não acusando.\n\n"
            "Não ameaçando.\n\n"
            "Apenas esperando Vincent finalmente atravessar.\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "PERSONALIDADE\n"
            "fechado emocionalmente\n"
            "agressivo sob pressão\n"
            "extremamente protetor\n"
            "desconfiado\n"
            "dificuldade em demonstrar culpa\n"
            "vive em estado constante de alerta\n"
            "tende a afastar pessoas antes que se aproximem demais\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "O QUE VALE SERENO ENXERGA NELE\n"
            "A cidade não quer punir Vincent.\n"
            "Ela quer ver até onde alguém consegue fugir da própria consciência antes de quebrar.\n\n"
            "’Algumas culpas não perseguem você.\n"
            "Elas apenas esperam você parar de correr.’"
        ),
        "Mirela": (
            "🌧️ MIRELA AHN\n"
            "’A que enxergava antes dos outros’\n\n"
            "Mirela desenhou Vale Sereno pela primeira vez aos onze anos.\n\n"
            "Ela ainda guarda o desenho.\n\n"
            "Uma rua coberta por neblina.\n"
            "Uma igreja sem portas.\n"
            "E alguém parado atrás de uma janela.\n\n"
            "Na época, os pais acharam criativo. A professora chamou de imaginação fértil. Algumas crianças da escola começaram a evitar sentar perto dela depois de um tempo, principalmente porque Mirela tinha o hábito estranho de dizer coisas que acabavam acontecendo dias depois.\n\n"
            "Pequenas coisas no começo.\n\n"
            "Um professor quebrando o braço.\n"
            "Uma colega perdendo o cachorro.\n"
            "Uma tempestade chegando antes da previsão.\n\n"
            "Nada grande o suficiente para assustar adultos.\n\n"
            "Mas o suficiente para isolar ela.\n\n"
            "Mirela cresceu aprendendo a esconder certas percepções. Não porque queria mentir, mas porque percebeu cedo demais que as pessoas ficam desconfortáveis perto de alguém que parece notar coisas que ainda não aconteceram.\n\n"
            "Ela não tinha amigos próximos.\n"
            "Nunca manteve relacionamentos longos.\n"
            "E, às vezes, tinha dificuldade de distinguir memória de sensação.\n\n"
            "Existiam lugares que ela ‘lembrava’ sem nunca ter visitado.\n\n"
            "Vale Sereno era um deles.\n\n"
            "Conforme envelhecia, os sonhos pioravam. Não eram pesadelos comuns. Eram consistentes demais. Sempre a mesma cidade. As mesmas ruas molhadas. O mesmo lago imóvel. E uma sensação constante de que alguém estava observando ela de algum lugar alto.\n\n"
            "Então os desenhos começaram a mudar.\n\n"
            "Antes, Mirela desenhava a cidade.\n"
            "Agora desenhava pessoas.\n\n"
            "Pessoas que nunca conheceu.\n\n"
            "E o pior:\n"
            "algumas delas começaram a aparecer na vida real.\n\n"
            "Ela reconheceu um professor universitário dias antes de conhecer ele. Viu uma mulher chorando no metrô exatamente da mesma forma que tinha desenhado semanas antes. E certa vez desenhou o próprio rosto refletido em um espelho quebrado…\n\n"
            "com alguém parado atrás dela.\n\n"
            "Quando a carta chegou, Mirela não ficou surpresa.\n\n"
            "No fundo, acho que uma parte dela estava esperando há anos.\n\n"
            "O envelope continha apenas uma fotografia da cidade e uma frase escrita à mão:\n\n"
            "’Você já esteve aqui antes.’\n\n"
            "Ela deveria ter achado absurdo.\n"
            "Mas não achou.\n\n"
            "Porque, olhando para aquela imagem, Mirela sentiu algo pior do que medo.\n\n"
            "Reconhecimento.\n\n"
            "Como se parte dela realmente pertencesse àquele lugar.\n\n"
            "Depois disso, começou a notar detalhes estranhos nos próprios sonhos. Lugares antes vazios agora tinham pessoas observando ela. Janelas apareciam abertas. E algumas figuras começaram a chamar ela pelo nome.\n\n"
            "Sempre o mesmo horário.\n"
            "Sempre a mesma chuva.\n\n"
            "2:13.\n\n"
            "Mirela não contou isso para ninguém.\n\n"
            "Principalmente porque existe uma pergunta que ela evita fazer para si mesma:\n\n"
            "E se Vale Sereno não estiver chamando ela…\n"
            "mas tentando trazer ela de volta?\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "PERSONALIDADE\n"
            "quieta\n"
            "introspectiva\n"
            "extremamente perceptiva\n"
            "emocionalmente distante\n"
            "dificuldade em se conectar com pessoas\n"
            "sensível ao ambiente ao redor\n"
            "tende a perceber mudanças antes dos outros\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "O QUE VALE SERENO ENXERGA NELA\n"
            "A cidade não vê Mirela como visitante.\n"
            "Ela vê alguém que consegue ouvir coisas que o resto do mundo aprendeu a ignorar."
        ),
        "Arthur": (
            "🌧️ ARTHUR VASCONCELLOS\n"
            "’O que nunca conseguiu ir embora’\n\n"
            "Arthur lembra exatamente do cheiro da chuva naquele dia.\n\n"
            "Gasolina.\n"
            "Asfalto molhado.\n"
            "Ferrugem.\n\n"
            "É estranho o que permanece na memória quando todo o resto começa a desaparecer.\n\n"
            "Ele e Helena estavam viajando havia horas quando a estrada sumiu na neblina. Não literalmente — pelo menos era o que Arthur dizia para si mesmo durante anos — mas havia algo errado naquele caminho. As placas pareciam antigas demais, o rádio falhava sempre no mesmo ponto e, em determinado momento, Helena perguntou:\n\n"
            "’Você também sente como se já tivéssemos passado aqui antes?’\n\n"
            "Arthur riu na época.\n\n"
            "Hoje, essa lembrança faz ele perder o sono.\n\n"
            "Vale Sereno apareceu no meio da chuva como um erro. Pequena. Silenciosa. Vazia demais. Eles deveriam apenas atravessar a cidade e continuar viagem, mas o carro começou a falhar perto da entrada. Nenhum sinal no celular. Nenhuma pessoa nas ruas.\n\n"
            "Só chuva.\n\n"
            "Arthur lembra de quase tudo daquela noite.\n\n"
            "Quase.\n\n"
            "Porque existem partes que desapareceram completamente da memória dele.\n\n"
            "Ele lembra da igreja.\n"
            "Do lago.\n"
            "Das janelas.\n\n"
            "Mas não lembra do momento exato em que perdeu Helena.\n\n"
            "Um instante ela estava ao lado dele.\n"
            "No outro—\n"
            "não estava mais.\n\n"
            "Sem grito.\n"
            "Sem luta.\n"
            "Sem explicação.\n\n"
            "Arthur procurou por ela durante horas. Talvez dias. Talvez semanas. Em Vale Sereno, o tempo nunca parece funcionar corretamente nas lembranças dele. A cidade mudava enquanto ele andava. Corredores terminavam em lugares impossíveis. Ruas pareciam diferentes dependendo da direção que olhava.\n\n"
            "E toda vez que ele quase encontrava Helena…\n\n"
            "alguma coisa mudava.\n\n"
            "Uma porta desaparecia.\n"
            "A chuva aumentava.\n"
            "O lago aparecia novamente.\n\n"
            "Arthur saiu da cidade sozinho.\n\n"
            "Pelo menos é nisso que tenta acreditar.\n\n"
            "Os anos seguintes destruíram lentamente tudo ao redor dele. O casamento virou caso arquivado. A polícia encerrou as buscas. Amigos pararam de ligar. A família de Helena começou a olhar para Arthur como se ele escondesse alguma coisa.\n\n"
            "Talvez escondesse.\n\n"
            "Porque existe uma verdade que ele nunca contou:\n\n"
            "Às vezes, durante a madrugada, ele ouve Helena andando pela casa.\n\n"
            "Passos molhados.\n"
            "Lentos.\n\n"
            "Como se tivesse acabado de voltar da chuva.\n\n"
            "Arthur nunca acende a luz quando isso acontece.\n\n"
            "Porque parte dele tem medo de descobrir que não existe ninguém ali.\n\n"
            "E outra parte…\n"
            "tem medo de descobrir que existe.\n\n"
            "Ele começou a pesquisar Vale Sereno obsessivamente depois disso. Colecionou mapas antigos, jornais apagados da internet, fotografias queimadas parcialmente pela água. Quanto mais investigava, mais percebia algo impossível:\n\n"
            "A cidade parecia mudar retroativamente.\n\n"
            "Fotos antigas mostravam construções que não existiam antes.\n"
            "Mapas apareciam diferentes dependendo do horário.\n"
            "E, em algumas imagens, Helena ainda aparecia ao fundo.\n\n"
            "Observando.\n"
            "Nunca envelhecendo.\n\n"
            "Arthur não sabe dizer exatamente quando a obsessão começou a consumir ele. Talvez tenha sido no momento em que percebeu que já não procurava apenas a esposa.\n\n"
            "Procurava confirmação.\n"
            "Queria saber se realmente saiu daquela cidade.\n\n"
            "Porque existem noites em que Arthur acorda sentado dentro do carro, com as mãos no volante, chuva batendo nos vidros e a sensação absurda de que ainda está dirigindo em direção a Vale Sereno.\n\n"
            "Como se a estrada nunca tivesse terminado.\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "PERSONALIDADE\n"
            "extremamente obsessivo\n"
            "emocionalmente desgastado\n"
            "inteligente\n"
            "investigativo\n"
            "dificuldade em abandonar o passado\n"
            "vive preso entre memória e culpa\n"
            "tende a ignorar o próprio sofrimento em busca de respostas\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "O QUE VALE SERENO ENXERGA NELE\n"
            "Arthur acredita que quer encontrar Helena.\n\n"
            "Mas a cidade percebe algo pior:\n\n"
            "Ele precisa descobrir se algum dia realmente conseguiu sair.\n\n"
            "’Algumas cidades são lugares.\n"
            "Outras…\n"
            "são perguntas sem resposta.’"
        ),
        "Lyra": (
            "🌧️ LYRA VIDAL\n"
            "’A que não deveria estar viva’\n\n"
            "Lyra morreu aos dezessete anos.\n\n"
            "Pelo menos é isso que o relatório médico diz.\n\n"
            "Acidente automobilístico.\n"
            "Perda massiva de sangue.\n"
            "Parada cardíaca no trajeto até o hospital.\n\n"
            "Horário da morte:\n"
            "2:13 da manhã.\n\n"
            "Ela nunca gostou desse detalhe.\n\n"
            "Lyra lembra do impacto.\n"
            "Do vidro quebrando.\n"
            "Da chuva.\n"
            "Da sensação do próprio corpo ficando distante.\n\n"
            "E depois—\n\n"
            "silêncio.\n\n"
            "Não escuridão.\n\n"
            "Silêncio.\n\n"
            "Como se o mundo tivesse parado de existir por alguns segundos.\n\n"
            "Ela lembra de abrir os olhos horas depois dentro do necrotério. Não deveria ser possível. Os médicos chamaram de milagre. A família chamou de bênção. Jornais locais chegaram a publicar pequenas matérias sobre ‘a garota que voltou’.\n\n"
            "Mas Lyra nunca sentiu que voltou completamente.\n\n"
            "Alguma coisa ficou errada depois disso.\n\n"
            "No começo eram detalhes pequenos.\n\n"
            "Reflexos demorando para copiar movimentos.\n"
            "Sensação constante de estar sendo observada.\n"
            "Pessoas olhando para ela por tempo demais em lugares públicos.\n\n"
            "Então vieram os sonhos.\n\n"
            "Sempre a mesma cidade.\n"
            "Chuva.\n"
            "Neblina.\n"
            "Janelas iluminadas.\n\n"
            "E alguém esperando ela perto de um lago.\n\n"
            "Lyra começou a desenvolver um hábito estranho depois do acidente: anotar coisas que ‘sentia’ antes de acontecerem. Não exatamente previsões. Mais como impressões.\n\n"
            "’Hoje vai chover às 14h.’\n"
            "’A mulher do metrô vai chorar.’\n"
            "’Não entre naquela rua.’\n\n"
            "Na maioria das vezes, acontecia.\n\n"
            "Ela parou de comentar isso com outras pessoas quando percebeu que começavam a se afastar lentamente.\n\n"
            "Mas o pior veio alguns anos depois.\n\n"
            "Ela começou a esquecer partes da própria vida.\n\n"
            "Não memórias específicas.\n"
            "Conexões emocionais.\n\n"
            "Como se certas coisas deixassem de parecer reais com o passar do tempo. Fotos da infância pareciam pertencer a outra pessoa. Lugares importantes não despertavam mais nada.\n\n"
            "E, às vezes, olhando no espelho tarde da noite…\n\n"
            "Lyra tinha a sensação absurda de que o reflexo reconhecia ela melhor do que ela reconhecia a si mesma.\n\n"
            "A carta chegou numa madrugada chuvosa.\n\n"
            "Sem surpresa.\n"
            "Sem medo.\n\n"
            "No fundo, ela já esperava.\n\n"
            "Dentro havia apenas uma fotografia de Vale Sereno e uma frase escrita à mão:\n\n"
            "’Você quase ficou aqui da primeira vez.’\n\n"
            "Lyra leu aquilo sem conseguir respirar direito.\n\n"
            "Porque existia uma verdade escondida que ela nunca contou para ninguém:\n\n"
            "Durante os segundos em que esteve morta…\n\n"
            "ela viu a cidade.\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "E alguém observando ela do outro lado da água.\n\n"
            "Desde então, às vezes sente que alguma coisa continua chamando ela lentamente de volta. Não de forma agressiva. Não como ameaça.\n\n"
            "Como reconhecimento.\n\n"
            "Como se Vale Sereno não enxergasse Lyra como visitante.\n"
            "Mas como alguém que pertence ao lugar mais do que ao resto do mundo.\n\n"
            "E existe uma pergunta que ela evita fazer para si mesma desde o acidente:\n\n"
            "E se ela realmente nunca tiver voltado?\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "PERSONALIDADE\n"
            "calma demais em situações erradas\n"
            "intuitiva\n"
            "emocionalmente desconectada às vezes\n"
            "silenciosa\n"
            "observadora\n"
            "aceita o horror rápido demais\n"
            "dificuldade em sentir pertencimento\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "O QUE VALE SERENO ENXERGA NELA\n"
            "A cidade não quer atrair Lyra.\n"
            "Ela quer terminar algo que começou naquela noite.\n\n"
            "’Existem pessoas que sobrevivem à morte.\n"
            "E existem pessoas que apenas continuam depois dela.’"
        ),
    }
    profile = profiles.get(character)
    if not profile:
        profile = f"Não há ficha disponível para {character}."
    return profile


async def handle_minha_gota(send_func, user_id: int) -> None:
    existing = VALE_GAMES.get(user_id) or get_vale(user_id)
    if not existing:
        await send_func("Você ainda não iniciou Vale Sereno. Use `/procurar gotas` para começar.")
        return
    if existing.get("awaiting"):
        await send_func("Sua sessão de Vale Sereno ainda está em andamento. Responda com A, B, C ou D para continuar, e depois use /minha gota.")
        return
    result = existing.get("result")
    if not result:
        await send_func("Nenhum resultado de Vale Sereno foi encontrado. Use `/procurar gotas` para iniciar uma nova sessão.")
        return
    profile_text = get_vale_profile_text(result)
    await send_long_message(send_func, profile_text)


async def start_vale_sereno(send_func, user_id: int) -> None:
    # Initialize Vale Sereno state in memory
    existing = VALE_GAMES.get(user_id) or get_vale(user_id)
    if existing and existing.get("awaiting"):
        VALE_GAMES[user_id] = existing
        await send_func("Você já está em uma sessão de Vale Sereno. Responda A, B, C ou D para prosseguir.")
        return
    if existing:
        VALE_GAMES.pop(user_id, None)
        try:
            delete_vale(user_id)
        except Exception:
            pass
    state = _init_vale_state()
    VALE_GAMES[user_id] = state
    persist_vale(user_id, state)
    sit = VALE_SITUATIONS[0]
    await send_long_message(send_func, f"**{sit['title']}**\n\n{sit['text']}")
    opts = "\n".join([f"{k}) {v}" for k, v in sit['options'].items()])
    await send_long_message(send_func, opts)


@bot.command(name="procurar", description="Inicia Vale Sereno (use: /procurar gotas)")
async def procurar(ctx, tema: str = None) -> None:
    if ctx.author.bot:
        return
    if tema and tema.lower() != "gotas":
        await ctx.send("Use `/procurar gotas` para iniciar a experiência Vale Sereno.")
        return
    await start_vale_sereno(ctx.send, ctx.author.id)


@bot.tree.command(name="procurar", description="Inicia Vale Sereno (argumento: gotas)")
@app_commands.describe(tema="Subcomando, use 'gotas' para iniciar")
async def procurar_slash(interaction: discord.Interaction, tema: Optional[str] = None) -> None:
    if interaction.user.bot:
        return
    responder = InteractionResponder(interaction)
    if tema and tema.lower() != "gotas":
        await responder.send("Use /procurar gotas para iniciar a experiência Vale Sereno.")
        return
    await start_vale_sereno(responder.send, interaction.user.id)


@bot.command(name="minha", description="Mostra a ficha do seu personagem gerado em Vale Sereno (use: /minha gota)")
async def minha(ctx, tema: str = None) -> None:
    if ctx.author.bot:
        return
    if not tema or tema.lower() not in {"gota", "gotas"}:
        await ctx.send("Use `/minha gota` para ver a sua ficha de Vale Sereno.")
        return
    await handle_minha_gota(ctx.send, ctx.author.id)


@bot.tree.command(name="minha", description="Mostra a ficha do seu personagem gerado em Vale Sereno.")
@app_commands.describe(tema="Use 'gota' para ver a ficha")
async def minha_slash(interaction: discord.Interaction, tema: Optional[str] = None) -> None:
    if interaction.user.bot:
        return
    responder = InteractionResponder(interaction)
    if not tema or tema.lower() not in {"gota", "gotas"}:
        await responder.send("Use /minha gota para ver a sua ficha de Vale Sereno.")
        return
    await handle_minha_gota(responder.send, interaction.user.id)


@bot.command(name="iniciar", description="Começa a jornada de Gaia com Ikarus.")
async def iniciar(ctx, nome: str = None) -> None:
    if ctx.author.bot:
        return
    await handle_iniciar(ctx.send, ctx.author.id, nome)


@bot.tree.command(name="iniciar", description="Começa a jornada de Gaia com Ikarus.")
async def iniciar_slash(interaction: discord.Interaction, nome: Optional[str] = None) -> None:
    if interaction.user.bot:
        return
    responder = InteractionResponder(interaction)
    await handle_iniciar(responder.send, interaction.user.id, nome)


@bot.command(name="continuar", description="Continua a história do ponto onde você parou.")
async def continuar(ctx) -> None:
    if ctx.author.bot:
        return
    await handle_continuar(ctx.send, ctx.author.id)


@bot.tree.command(name="continuar", description="Continua a história do ponto onde você parou.")
async def continuar_slash(interaction: discord.Interaction) -> None:
    if interaction.user.bot:
        return
    responder = InteractionResponder(interaction)
    await handle_continuar(responder.send, interaction.user.id)


@bot.command(name="status", description="Mostra um resumo do seu progresso e tendências.")
async def status(ctx) -> None:
    if ctx.author.bot:
        return
    await handle_status(ctx.send, ctx.author.id)


@bot.tree.command(name="status", description="Mostra um resumo do seu progresso e tendências.")
async def status_slash(interaction: discord.Interaction) -> None:
    if interaction.user.bot:
        return
    responder = InteractionResponder(interaction)
    await handle_status(responder.send, interaction.user.id)


@bot.command(name="capitulo", description="Escolhe um capítulo e reinicia o progresso para ele. Opcional: bloco inicial.")
async def capitulo(ctx, chapter: int, bloco: int = None) -> None:
    if ctx.author.bot:
        return
    await handle_choose_chapter(ctx.send, ctx.author.id, chapter, bloco)


@bot.tree.command(name="capitulo", description="Escolhe um capítulo e reinicia o progresso para ele. Opcional: bloco inicial.")
async def capitulo_slash(interaction: discord.Interaction, chapter: int, bloco: Optional[int] = None) -> None:
    if interaction.user.bot:
        return
    responder = InteractionResponder(interaction)
    await handle_choose_chapter(responder.send, interaction.user.id, chapter, bloco)


@bot.command(name="reiniciar", description="Reinicia toda a sua jornada do zero.")
async def reiniciar(ctx) -> None:
    if ctx.author.bot:
        return
    await handle_reiniciar(ctx.send, ctx.author.id)


@bot.tree.command(name="reiniciar", description="Reinicia toda a sua jornada do zero.")
async def reiniciar_slash(interaction: discord.Interaction) -> None:
    if interaction.user.bot:
        return
    responder = InteractionResponder(interaction)
    await handle_reiniciar(responder.send, interaction.user.id)


@bot.event
async def on_message(message: discord.Message) -> None:
    print(f"[DEBUG] Mensagem recebida de {message.author}: {message.content}")
    if message.author.bot:
        print("[DEBUG] Ignorando mensagem de bot")
        return
    content = message.content.strip()
    print(f"[DEBUG] Conteúdo: {content}")
    
    # Check for commands first
    if content.startswith("/") or content.startswith("&"):
        print("[DEBUG] É um comando com prefixo / ou &")
        await bot.process_commands(message)
        return

    # Vale Sereno: handle interactive /procurar gotas responses first (A-D)
    vale_state = VALE_GAMES.get(message.author.id)
    if vale_state and vale_state.get("awaiting"):
        choice = None
        cleaned = content.strip().upper()
        if cleaned and cleaned[0] in {"A","B","C","D"}:
            choice = cleaned[0]
        if choice:
            # process choice
            stage = vale_state["stage"]
            _tally_choice(vale_state, stage, choice)
            vale_state["choices"].append({"stage": stage, "choice": choice})
            if stage < 3:
                vale_state["stage"] = stage + 1
                persist_vale(message.author.id, vale_state)
                # send next situation
                sit = VALE_SITUATIONS[vale_state["stage"] - 1]
                await send_long_message(message.channel.send, f"**{sit['title']}**\n\n{sit['text']}")
                opts = "\n".join([f"{k}) {v}" for k, v in sit['options'].items()])
                await send_long_message(message.channel.send, opts)
                return
            # finalize
            result = _determine_vale_result(vale_state)
            vale_state["result"] = result
            vale_state["awaiting"] = False
            persist_vale(message.author.id, vale_state)
            # build narrative result
            char = result["character"]
            dom = result["dominant_attributes"]
            narrative = [
                "Vale Sereno não escolhe pessoas aleatoriamente.",
                "Ela encontra aquilo que cada um tentou esconder por tempo demais.",
                "",
                f"O lugar sussurra o nome: {char}.",
                "",
                "Porque a cidade percebeu padrões que você mesmo talvez não reconheça.",
                "",
                "Perfil psicológico:",
            ]
            # short descriptions for characters
            char_desc = {
                "Noah": "Racionalidade, controle e medo de perder a sanidade.",
                "Elena": "Apego emocional, culpa e esperança destrutiva.",
                "Vincent": "Culpa reprimida, paranoia e violência emocional.",
                "Mirela": "Sensibilidade paranormal, percepção elevada e isolamento.",
                "Arthur": "Obsessão, investigação compulsiva e desgaste emocional.",
                "Lyra": "Atração pelo desconhecido, desconexão e aceitação do horror.",
            }
            narrative.append(char_desc.get(char, "Um escolhido estranho."))
            if dom:
                narrative.append("")
                narrative.append("Traços invisíveis detectados:")
                for a in dom:
                    narrative.append(f"- {a.replace('_',' ')}")
            narrative.append("")
            narrative.append("A cidade adaptou o horror só para você. Ela viu algo que você tentou esconder.")
            await send_long_message(message.channel.send, "\n".join(narrative))
            return
    
    player_data = get_player(message.author.id)
    if player_data and player_data.get("awaiting_choice"):
        chapter = player_data["chapter"]
        block = player_data["block"]
        current = get_block(chapter, block)
        if current and current.get("requires_answer"):
            if is_valid_answer(content, current["requires_answer"]):
                player_data["awaiting_choice"] = False
                next_value = current.get("next")
                if next_value is not None:
                    if isinstance(next_value, (list, tuple)) and len(next_value) == 2:
                        player_data["chapter"], player_data["block"] = next_value
                    else:
                        player_data["block"] = next_value
                persist_player(message.author.id, player_data)
                await message.channel.send("Resposta correta. Continuando...")
                await send_current_block(message.author.id, message.channel.send, player_data, should_persist=True)
                return
            await message.channel.send(current.get("answer_failure_text", "Resposta incorreta. Tente novamente."))
            return

    choice = parse_choice(content)
    if not choice:
        print("[DEBUG] Não é uma escolha A/B/C")
        return
    print(f"[DEBUG] É uma escolha A/B/C: {choice}")
    print(f"[DEBUG] Player data: {player_data}")
    if not player_data or not player_data.get("awaiting_choice"):
        print("[DEBUG] Sem jogo iniciado ou sem escolha pendente")
        return
    chapter = player_data["chapter"]
    block = player_data["block"]
    current = get_block(chapter, block)
    if not current or "choices" not in current:
        return
    selected = current["choices"].get(choice)
    if not selected:
        await message.channel.send("Escolha inválida. Responda apenas A, B ou C.")
        return
    update_attributes(player_data["attributes"], selected["points"])
    player_data["choices"].append({
        "chapter": chapter,
        "block": block,
        "choice": choice,
    })
    player_data["awaiting_choice"] = False
    next_block = selected.get("next", current.get("next"))
    if next_block is not None:
        if isinstance(next_block, (list, tuple)) and len(next_block) == 2:
            if chapter == 1 and next_block[0] == 2:
                await send_long_message(message.channel.send, get_chapter_1_end_text(player_data["attributes"]))
            player_data["chapter"], player_data["block"] = next_block
        else:
            player_data["block"] = next_block
    persist_player(message.author.id, player_data)
    await message.channel.send(f"Você escolheu {choice}. Continuando...")
    await send_current_block(message.author.id, message.channel.send, player_data, should_persist=True)


if __name__ == "__main__":
    create_lock_file()
    try:
        bot.run(TOKEN)
    finally:
        remove_lock_file()
