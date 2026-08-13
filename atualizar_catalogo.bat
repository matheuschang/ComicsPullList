@echo off
REM ============================================================================
REM  Atualizacao semanal do catalogo do Pull List.
REM
REM  Raspa as ultimas 4 semanas + 1 a frente da LOCG, faz merge no catalogo
REM  existente (nao re-raspa o ano), regenera as estatisticas do dashboard e
REM  sobe pro GitHub -- o Pages republica e todos os clientes pegam o catalogo
REM  novo. Pode agendar no Agendador de Tarefas do Windows (semanal).
REM
REM  Cloudflare: abre um Chrome de depuracao com perfil proprio (o clearance
REM  fica salvo). Se aparecer o desafio, passe-o NESSA janela -- o script espera.
REM
REM  Ajuste os 3 caminhos abaixo se necessario.
REM ============================================================================

setlocal
set "PROJ=C:\Users\mathe\OneDrive\Documentos\dev\comics_tracker"
set "PY=C:\Users\mathe\OneDrive\Documentos\dev\comics_releases\venv\Scripts\python.exe"
set "CHROME=C:\Program Files\Google\Chrome\Application\chrome.exe"
set "PERFIL=C:\Users\mathe\chrome-locg"

cd /d "%PROJ%"

echo === 1/4  Abrindo Chrome de depuracao (passe o Cloudflare se aparecer) ===
start "" "%CHROME%" --remote-debugging-port=9222 --user-data-dir="%PERFIL%" "https://leagueofcomicgeeks.com/comics/new-comics"
timeout /t 10 /nobreak >nul

echo === 2/4  Atualizando catalogo (ultimas 4 semanas + 1 a frente) ===
"%PY%" ingest\from_locg.py --anexar 127.0.0.1:9222 --atualizar --atras 4 --frente 1
if errorlevel 1 (
  echo [ERRO] a raspagem falhou -- nada foi enviado.
  goto :fim
)

echo === 3/4  Regenerando estatisticas do dashboard ===
"%PY%" ingest\stats.py

echo === 4/4  Enviando ao repositorio ===
git add web/data
git diff --cached --quiet
if %errorlevel%==0 (
  echo Nada mudou no catalogo -- sem commit.
  goto :fim
)
git commit -m "Atualiza catalogo (semanal)"
git push

:fim
echo Concluido em %date% %time%.
endlocal
