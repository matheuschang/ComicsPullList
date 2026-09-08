@echo off
REM ============================================================================
REM  Atualizacao semanal das DUAS bases do Pull List.
REM
REM  1) Base do SITE (web/data, commitada): raspa as ultimas 4 semanas + 1 a
REM     frente da LOCG, faz merge no catalogo existente (nao re-raspa o ano),
REM     regenera as estatisticas e sobe pro GitHub -- o Pages republica.
REM  2) Base de FICHAS (fichas/, local, fora do repo): visita a pagina de
REM     detalhe das edicoes novas e alimenta o CSV de cadastro manual.
REM
REM  A ordem importa: a lista de trabalho das fichas SAI do catalogo do site,
REM  entao o site atualiza primeiro e as fichas pegam os links novos depois.
REM
REM  Cloudflare: abre um Chrome de depuracao com perfil proprio (o clearance
REM  fica salvo). Se aparecer o desafio, passe-o NESSA janela -- o script espera.
REM
REM  Ajuste os caminhos e os tetos abaixo se necessario.
REM ============================================================================

setlocal
set "PROJ=C:\Users\mathe\OneDrive\Documentos\dev\comics_tracker"
set "PY=C:\Users\mathe\OneDrive\Documentos\dev\comics_releases\venv\Scripts\python.exe"
set "CHROME=C:\Program Files\Google\Chrome\Application\chrome.exe"
set "PERFIL=C:\Users\mathe\chrome-locg"

REM Teto por rodada da base de fichas. Numa semana normal chegam ~40 edicoes
REM novas, muito abaixo do teto. O teto existe para o caso de a base estar
REM atrasada (na primeira vez ela esta VAZIA, com ~2700 edicoes a fazer): assim
REM a rodada semanal nao vira uma maratona de horas e a base se aproxima do
REM catalogo sozinha, 400 por semana. Para adiantar o historico de uma vez, rode
REM a mao: python ingest\fichas_locg.py --anexar 127.0.0.1:9222 --limite 300
set "TETO_FICHAS=400"

REM Capas: ~220 KB cada. Deixe CAPAS=0 se nao quiser gastar disco/banda agora.
set "CAPAS=1"
set "TETO_CAPAS=400"

cd /d "%PROJ%"

echo === 1/6  Abrindo Chrome de depuracao (passe o Cloudflare se aparecer) ===
start "" "%CHROME%" --remote-debugging-port=9222 --user-data-dir="%PERFIL%" "https://leagueofcomicgeeks.com/comics/new-comics"
timeout /t 10 /nobreak >nul

echo === 2/6  Base do site: catalogo (ultimas 4 semanas + 1 a frente) ===
"%PY%" ingest\from_locg.py --anexar 127.0.0.1:9222 --atualizar --atras 4 --frente 1
if errorlevel 1 (
  echo [ERRO] a raspagem falhou -- nada foi enviado.
  goto :fim
)

echo === 3/6  Base do site: estatisticas do dashboard ===
"%PY%" ingest\stats.py

echo === 4/6  Base do site: enviando ao repositorio ===
git add web/data
git diff --cached --quiet
if %errorlevel%==0 (
  echo Nada mudou no catalogo -- sem commit.
) else (
  git commit -m "Atualiza catalogo (semanal)"
  git push
)

REM Daqui pra baixo e a base LOCAL de fichas: nao vai pro repo, e um problema
REM aqui nao pode desfazer o push do site que ja aconteceu acima.
echo === 5/6  Base de fichas: edicoes novas (teto de %TETO_FICHAS%) ===
"%PY%" ingest\fichas_locg.py --anexar 127.0.0.1:9222 --limite %TETO_FICHAS%
if errorlevel 1 echo [aviso] a base de fichas nao atualizou -- o site ja foi enviado.

if "%CAPAS%"=="1" (
  echo === 6/6  Base de fichas: capas que faltam (teto de %TETO_CAPAS%) ===
  "%PY%" ingest\fichas_locg.py --so-capas --limite %TETO_CAPAS%
) else (
  echo === 6/6  Capas desligadas ^(CAPAS=0^) -- pulando ===
)

:fim
echo Concluido em %date% %time%.
endlocal
