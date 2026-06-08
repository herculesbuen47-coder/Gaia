import { config } from "dotenv";
import {
  ChannelType,
  Client,
  GatewayIntentBits,
  Partials,
  REST,
  Routes,
  SlashCommandBuilder,
  Events,
} from "discord.js";

config();

const TOKEN = process.env.DISCORD_TOKEN;
const CLIENT_ID = process.env.DISCORD_CLIENT_ID;
const GUILD_ID = process.env.DISCORD_GUILD_ID; // opcional para registrar comandos em guild

// COLOQUE AQUI O ID DO BOT ROLLEM
// Exemplo: const ROLLEM_BOT_ID = "123456789012345678";
const ROLLEM_BOT_ID = process.env.ROLLEM_BOT_ID || "240732567744151553";

if (!TOKEN) {
  console.error("Erro: defina DISCORD_TOKEN no .env.");
  process.exit(1);
}

if (!CLIENT_ID) {
  console.warn("Aviso: DISCORD_CLIENT_ID não definido. O comando será registrado depois que o bot estiver pronto.");
}

const preparedBlackFlashByChannel = new Map();

const client = new Client({
  intents: [
    GatewayIntentBits.Guilds,
    GatewayIntentBits.GuildMessages,
    GatewayIntentBits.MessageContent,
  ],
  partials: [Partials.Channel],
});

function pickThreeSecretNumbers() {
  const numbers = new Set();
  while (numbers.size < 3) {
    numbers.add(Math.floor(Math.random() * 20) + 1);
  }
  return Array.from(numbers).sort((a, b) => a - b);
}

function extractD20Result(message) {
  const lines = [];
  if (message.content) {
    lines.push(message.content);
  }
  if (message.embeds?.length) {
    for (const embed of message.embeds) {
      if (embed.title) lines.push(embed.title);
      if (embed.description) lines.push(embed.description);
      if (embed.fields?.length) {
        for (const field of embed.fields) {
          if (field.name) lines.push(field.name);
          if (field.value) lines.push(field.value);
        }
      }
    }
  }

  const text = lines.join("\n");
  console.log(`[DEBUG] extractD20Result text=${JSON.stringify(text)}`);

  const patterns = [
    /\b(?:resultado|result|roll|rolagem|dado|d20)[^0-9\n]{0,15}(\d{1,2})/i,
    /\b(\d{1,2})\b(?=[^\n]*\b(?:resultado|result|roll|rolagem|dado|d20)\b)/i,
    /\b(?:d20)\b.*?(\d{1,2})/i,
    /\b(\d{1,2})\b\s*(?:→|->|:)\s*(?:\[\d+\]\s*)?1d20/i,
  ];

  for (const pattern of patterns) {
    const match = text.match(pattern);
    if (match) {
      const raw = match[1] ?? match[2];
      const value = Number(raw);
      if (!Number.isNaN(value) && value >= 1 && value <= 20) {
        return value;
      }
    }
  }

  const linesOnly = text.split("\n").map((line) => line.trim()).filter(Boolean);
  for (const line of linesOnly) {
    if (/\b(?:resultado|result|roll|rolagem|dado|d20)\b/i.test(line)) {
      const candidate = line.match(/\b(\d{1,2})\b(?!.*\d)/);
      if (candidate) {
        const value = Number(candidate[1]);
        if (!Number.isNaN(value) && value >= 1 && value <= 20) {
          return value;
        }
      }
    }
  }

  const allNumbers = [...text.matchAll(/\b(\d{1,2})\b/g)].map((m) => Number(m[1]));
  if (allNumbers.length === 1 && allNumbers[0] >= 1 && allNumbers[0] <= 20) {
    return allNumbers[0];
  }

  if (allNumbers.length > 0) {
    const filtered = allNumbers.filter((n) => n >= 1 && n <= 20);
    if (filtered.length > 0) {
      return filtered[filtered.length - 1];
    }
  }

  return null;
}

function extractRollingPlayerId(message) {
  if (message.mentions?.users?.size) {
    const firstMention = message.mentions.users.first();
    if (firstMention) return firstMention.id;
  }

  const content = message.content ?? "";
  const idMatch = content.match(/<@!?(\d{17,19})>/);
  if (idMatch) {
    return idMatch[1];
  }

  return null;
}

function isRollemMessage(message) {
  if (!message.author?.bot) return false;
  if (message.author.id === ROLLEM_BOT_ID) return true;

  const username = (message.author.username || "").toLowerCase();
  const tag = (message.author.tag || "").toLowerCase();
  if (username.includes("rollem") || tag.includes("rollem")) return true;

  return false;
}

function formatDramaticReply({ hit, secretNumbers, result }) {
  const secretList = secretNumbers.join(", ");
  if (hit) {
    return `**BLACK FLASH ACERTOU.**\n\nOs números do Êxtase eram: ${secretList}\nResultado do dado: ${result}\n\nO impacto acontece no intervalo impossível entre corpo, mente e paranormal.`;
  }
  return `**BLACK FLASH FALHOU.**\n\nOs números do Êxtase eram: ${secretList}\nResultado do dado: ${result}\n\nA energia falha por uma fração de segundo… e o golpe permanece humano.`;
}

client.once(Events.ClientReady, async () => {
  console.log(`Bot iniciado como ${client.user.tag}`);

  const prepareCommand = new SlashCommandBuilder()
    .setName("prepararblackflash")
    .setDescription("Prepara o Black Flash com três números secretos entre 1 e 20.");

  const blackflashCommand = new SlashCommandBuilder()
    .setName("blackflash")
    .setDescription("Prepara o Black Flash com três números secretos entre 1 e 20.");

  const stopCommand = new SlashCommandBuilder()
    .setName("pararblackflash")
    .setDescription("Para o Black Flash preparado neste canal.");

  const stopBlackFlashCommand = new SlashCommandBuilder()
    .setName("stopblackflash")
    .setDescription("Para o Black Flash preparado neste canal.");

  const rest = new REST({ version: "10" }).setToken(TOKEN);
  const applicationId = CLIENT_ID || client.user.id;
  const commandBody = [
    prepareCommand.toJSON(),
    blackflashCommand.toJSON(),
    stopCommand.toJSON(),
    stopBlackFlashCommand.toJSON(),
  ];

  try {
    if (GUILD_ID) {
      await rest.put(Routes.applicationCommands(applicationId), { body: [] });
      console.log("Comandos globais removidos antes do registro guild-only.");
      await rest.put(Routes.applicationGuildCommands(applicationId, GUILD_ID), {
        body: commandBody,
      });
      console.log(`Comando slash registrado no guild ${GUILD_ID}.`);
    } else {
      const guildIds = client.guilds.cache.map((guild) => guild.id);
      if (guildIds.length > 0) {
        for (const guildId of guildIds) {
          await rest.put(Routes.applicationGuildCommands(applicationId, guildId), {
            body: commandBody,
          });
          console.log(`Comando slash registrado no guild ${guildId}.`);
        }
      } else {
        await rest.put(Routes.applicationCommands(applicationId), {
          body: commandBody,
        });
        console.log("Comando slash registrado globalmente.");
      }
    }
  } catch (error) {
    console.error("Erro ao registrar comando slash:", error);
  }
});

client.on(Events.InteractionCreate, async (interaction) => {
  if (!interaction.isChatInputCommand()) return;
  console.log(`[DEBUG] interaction received command=${interaction.commandName} user=${interaction.user.tag} channel=${interaction.channelId}`);

  if (interaction.commandName === "prepararblackflash" || interaction.commandName === "blackflash") {
    if (interaction.channel?.type === ChannelType.DM) {
      await interaction.reply({
        content: "Use /prepararblackflash ou /blackflash em um canal do servidor, não no privado.",
        ephemeral: true,
      });
      return;
    }

    const userId = interaction.user.id;
    const secretNumbers = pickThreeSecretNumbers();
    const state = {
      secretNumbers,
      channelId: interaction.channelId,
      preparedAt: Date.now(),
      preparedBy: userId,
    };
    preparedBlackFlashByChannel.set(interaction.channelId, state);

    await interaction.reply({
      content: `**BLACK FLASH PREPARADO.**\n\nTrês números do Êxtase foram escolhidos para este canal. O preparo permanece ativo até que um jogador cancele com /pararblackflash ou /stopblackflash. Role seu d20 no Rollem quantas vezes quiser e o Black Flash continuará detectando o resultado.`,
    });
    return;
  }

  if (interaction.commandName === "pararblackflash" || interaction.commandName === "stopblackflash") {
    if (interaction.channel?.type === ChannelType.DM) {
      await interaction.reply({
        content: "Use /pararblackflash ou /stopblackflash em um canal do servidor, não no privado.",
        ephemeral: true,
      });
      return;
    }

    const channelId = interaction.channelId;
    console.log(`[DEBUG] pararblackflash invoked on channel=${channelId}`);
    const state = preparedBlackFlashByChannel.get(channelId);
    if (!state) {
      await interaction.reply({
        content: "Não há Black Flash preparado neste canal no momento.",
        ephemeral: true,
      });
      return;
    }

    preparedBlackFlashByChannel.delete(channelId);

    await interaction.reply({
      content: "**Black Flash foi parado.**\n\nA preparação foi removida e o canal não está mais esperando rolagens do Rollem.",
    });
    return;
  }
});

client.on(Events.MessageCreate, async (message) => {
  if (!isRollemMessage(message)) return;
  if (message.channel?.type === ChannelType.DM) return;

  console.log(`[DEBUG] Rollem message detected: author.id=${message.author.id} author.tag=${message.author.tag} channel=${message.channel.id}`);
  console.log(`[DEBUG] message.content=${JSON.stringify(message.content)}`);
  console.log(`[DEBUG] message.embeds=${JSON.stringify(message.embeds, null, 2)}`);
  console.log(`[DEBUG] message.mentions.users=${JSON.stringify(message.mentions?.users?.map((u) => ({ id: u.id, tag: u.tag })), null, 2)}`);

  const d20 = extractD20Result(message);
  if (d20 === null) {
    console.log("[DEBUG] Rollem message did not contain d20 result or pattern not matched.");
    return;
  }

  const state = preparedBlackFlashByChannel.get(message.channel.id);
  if (!state) {
    console.log("[DEBUG] Rollem detected but no Black Flash is prepared for this channel.");
    return;
  }
  console.log(`[DEBUG] Black Flash channel state active: preparedBy=${state.preparedBy} preparedAt=${state.preparedAt}`);

  const hit = state.secretNumbers.includes(d20);
  const reply = formatDramaticReply({
    hit,
    secretNumbers: state.secretNumbers,
    result: d20,
  });

  try {
    await message.channel.send({ content: reply });
  } catch (error) {
    console.error("Falha ao enviar a resposta do Black Flash:", error);
  }
});

client.login(TOKEN);
