# Execute este script no PowerShell para criar o ambiente virtual e instalar as dependências.
# Antes disso, instale o Python 3.11+ no sistema e certifique-se de que 'python' funcione no PowerShell.

python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
