@echo off
setlocal enabledelayedexpansion

REM ============================================================
REM  Reconciliation agent container launcher
REM  (MIPS - WMS - Oracle exception worklist, FastAPI on uvicorn)
REM
REM  Usage:
REM    start_container.bat            Smart start. Builds the image if missing,
REM                                   creates the container if missing, starts it
REM                                   if stopped, then drops into a shell.
REM    start_container.bat build      Force-rebuild the image (re-creates container).
REM    start_container.bat shell      Just exec a shell on the running container.
REM    start_container.bat run        Run the reconciliation once inside the container
REM                                   (writes the workbook/dashboard/JSON to output\).
REM    start_container.bat stop       Stop the container (keep it for fast restart).
REM    start_container.bat remove     Stop AND remove the container.
REM    start_container.bat logs       Follow container logs (Ctrl+C exits).
REM    start_container.bat status     Show container state.
REM    start_container.bat events ARGS
REM                                   Run "python -m recon_agent events ARGS" in the
REM                                   container, e.g. "events seed" or
REM                                   events set wms.item_order WL-0015 "Order Qty=800"
REM    start_container.bat sim        Start the source simulator container (operator app
REM                                   that publishes events) on http://localhost:8101/.
REM    start_container.bat sim-stop | sim-remove | sim-logs
REM ============================================================

REM -------- SETTINGS --------
set "IMAGE=recon-agent:latest"
set "CONTAINER_NAME=recon-agent"
REM Host port 8001 so this can run alongside other local containers on 8000.
set "HOST_PORT=8001"
set "CONTAINER_PORT=8000"
set "WORKDIR=/app"
set "ENV_FILE=.env"
REM The simulated event log (RECON_SOURCE=events) lives on a named volume, not the bind
REM mount: SQLite locking is unreliable across the Windows file share.
set "SIM_VOLUME=recon-sim"
set "SIM_MOUNT=/var/lib/recon-sim"
REM The source simulator runs as a second container from the same image, sharing that volume.
set "SIM_CONTAINER=recon-simulator"
set "SIM_HOST_PORT=8101"
set "SIM_CONTAINER_PORT=8100"

REM Run from the directory this .bat lives in, regardless of where it's invoked from.
cd /d "%~dp0"
set "HOST_PATH=%cd%"

REM Convert backslashes to forward slashes for the Docker Desktop bind mount.
REM The project folder is mounted at /app so the engine reads the daily extracts
REM (data\*.xlsx) from there and planner decisions persist in output\feedback.json.
set "DOCKER_PATH=%HOST_PATH:\=/%"

REM -------- Dispatch on first arg --------
set "ACTION=%~1"
if /I "%ACTION%"=="/?"      goto :usage
if /I "%ACTION%"=="-h"      goto :usage
if /I "%ACTION%"=="--help"  goto :usage
if /I "%ACTION%"=="help"    goto :usage
if /I "%ACTION%"=="build"   goto :build
if /I "%ACTION%"=="rebuild" goto :build
if /I "%ACTION%"=="shell"   goto :shell_only
if /I "%ACTION%"=="run"     goto :run_once
if /I "%ACTION%"=="stop"    goto :stop
if /I "%ACTION%"=="remove"  goto :remove
if /I "%ACTION%"=="down"    goto :remove
if /I "%ACTION%"=="logs"    goto :logs
if /I "%ACTION%"=="status"  goto :status
if /I "%ACTION%"=="ps"      goto :status
if /I "%ACTION%"=="events"  goto :events
if /I "%ACTION%"=="sim"         goto :sim
if /I "%ACTION%"=="sim-stop"    goto :sim_stop
if /I "%ACTION%"=="sim-remove"  goto :sim_remove
if /I "%ACTION%"=="sim-logs"    goto :sim_logs
if not "%ACTION%"=="" (
    echo Unknown action: %ACTION%
    echo.
    goto :usage
)

REM -------- Default flow --------
echo ============================================================
echo  Reconciliation agent container launcher
echo    image      : %IMAGE%
echo    container  : %CONTAINER_NAME%
echo    host path  : %HOST_PATH%
echo    mount      : %DOCKER_PATH%  ^-^>  %WORKDIR%
echo    port       : %HOST_PORT% (host) ^-^>  %CONTAINER_PORT% (container)
echo ============================================================

call :check_env

REM The engine needs the five daily extracts in the data folder.
if not exist "data\MIPS Order Data*.xlsx" (
    echo.
    echo [warn] No "MIPS Order Data*.xlsx" in %HOST_PATH%\data.
    echo        The worklist loads the MIPS, ODI and WMS extracts from that folder at startup.
    echo        Ignore this if .env sets RECON_SOURCE=events ^(simulated event feed^).
    echo.
)

REM Build image if missing.
docker image inspect %IMAGE% >NUL 2>&1
if errorlevel 1 (
    echo Image %IMAGE% not found. Building...
    docker build -t %IMAGE% .
    if errorlevel 1 (
        echo [start_container] image build failed.
        pause
        exit /b 1
    )
) else (
    echo Image %IMAGE% present. Skipping build. Use "start_container.bat build" to force rebuild.
)

REM Does a container with this name already exist?
docker ps -a -q -f "name=^%CONTAINER_NAME%$" | findstr . >NUL
if !ERRORLEVEL! equ 0 (
    REM Container exists. Running or stopped?
    for /f "delims=" %%i in ('docker inspect -f "{{.State.Status}}" %CONTAINER_NAME%') do set "STATUS=%%i"
    if /I "!STATUS!"=="running" (
        echo Container %CONTAINER_NAME% is already running. Attaching shell...
    ) else (
        echo Container %CONTAINER_NAME% exists but is !STATUS!. Starting...
        docker start %CONTAINER_NAME% >NUL
        if errorlevel 1 (
            echo [start_container] docker start failed.
            pause
            exit /b 1
        )
        REM The engine reconciles the extracts at startup; give uvicorn a moment to bind.
        ping -n 4 127.0.0.1 >NUL
    )
) else (
    call :create_container
    if errorlevel 1 exit /b 1
)

goto :shell

:build
call :check_env
echo Force-rebuilding %IMAGE%...
docker build -t %IMAGE% .
if errorlevel 1 (
    echo [start_container] image build failed.
    pause
    exit /b 1
)
REM Re-create the container so it picks up the fresh image.
docker rm -f %CONTAINER_NAME% >NUL 2>&1
call :create_container
if errorlevel 1 exit /b 1
goto :shell

:create_container
echo Creating new container %CONTAINER_NAME%...
set "ENV_ARG="
if exist "%ENV_FILE%" set "ENV_ARG=--env-file "%ENV_FILE%""
docker run -d ^
    --name %CONTAINER_NAME% ^
    !ENV_ARG! ^
    -v "%DOCKER_PATH%:%WORKDIR%" ^
    -v %SIM_VOLUME%:%SIM_MOUNT% ^
    -e RECON_EVENT_DB=%SIM_MOUNT%/events.db ^
    -e RECON_CORRECTIONS_URL=http://host.docker.internal:%SIM_HOST_PORT%/oic/corrections ^
    --add-host host.docker.internal:host-gateway ^
    -w %WORKDIR% ^
    -p %HOST_PORT%:%CONTAINER_PORT% ^
    --restart unless-stopped ^
    %IMAGE%
if errorlevel 1 (
    echo [start_container] docker run failed.
    pause
    exit /b 1
)
REM Server boot delay: the engine reconciles the extracts before uvicorn binds.
ping -n 5 127.0.0.1 >NUL
exit /b 0

:check_env
if not exist "%ENV_FILE%" (
    echo.
    echo [info] %ENV_FILE% not found in %HOST_PATH%. Continuing without it.
    echo        Narratives fall back to rule text and the assistant answers with engine data only.
    echo        Copy .env.example to .env to add AI credentials.
    echo.
)
exit /b 0

:shell_only
docker ps -q -f "name=^%CONTAINER_NAME%$" | findstr . >NUL
if errorlevel 1 (
    echo Container %CONTAINER_NAME% is not running. Use "start_container.bat" first.
    exit /b 1
)
goto :shell

:shell
echo.
echo Worklist: http://localhost:%HOST_PORT%/
echo API docs: http://localhost:%HOST_PORT%/api/docs
echo Dropping you into a shell inside the container as user 'recon'.
echo Type 'exit' to leave the shell. The container keeps running in the background.
echo Stop later with: start_container.bat stop    Remove with: start_container.bat remove
echo.
docker exec -it %CONTAINER_NAME% bash -lc "cd %WORKDIR% && exec bash"
goto :end

:run_once
docker ps -q -f "name=^%CONTAINER_NAME%$" | findstr . >NUL
if errorlevel 1 (
    echo Container %CONTAINER_NAME% is not running. Use "start_container.bat" first.
    exit /b 1
)
echo Running the reconciliation for the extracts in %HOST_PATH%\data...
docker exec -it %CONTAINER_NAME% python -m recon_agent run
goto :end

:events
docker ps -q -f "name=^%CONTAINER_NAME%$" | findstr . >NUL
if errorlevel 1 (
    echo Container %CONTAINER_NAME% is not running. Use "start_container.bat" first.
    exit /b 1
)
REM Pass every argument after "events" through, each re-quoted so "Order Qty=800" stays one argument.
set "EV_ARGS="
:events_collect
shift
if "%~1"=="" goto :events_run
set "EV_ARGS=!EV_ARGS! "%~1""
goto :events_collect
:events_run
docker exec -it %CONTAINER_NAME% python -m recon_agent events !EV_ARGS!
goto :end

:sim
docker image inspect %IMAGE% >NUL 2>&1
if errorlevel 1 (
    echo Image %IMAGE% not found. Run "start_container.bat build" first.
    exit /b 1
)
docker ps -a -q -f "name=^%SIM_CONTAINER%$" | findstr . >NUL
if !ERRORLEVEL! equ 0 (
    echo Starting %SIM_CONTAINER%...
    docker start %SIM_CONTAINER% >NUL
) else (
    echo Creating %SIM_CONTAINER%...
    set "ENV_ARG="
    if exist "%ENV_FILE%" set "ENV_ARG=--env-file "%ENV_FILE%""
    REM No health check: the image's check probes the worklist port, not the simulator's.
    docker run -d ^
        --name %SIM_CONTAINER% ^
        !ENV_ARG! ^
        -v "%DOCKER_PATH%:%WORKDIR%" ^
        -v %SIM_VOLUME%:%SIM_MOUNT% ^
        -e RECON_EVENT_DB=%SIM_MOUNT%/events.db ^
        -e RECON_WORKLIST_URL=http://localhost:%HOST_PORT%/ ^
        -w %WORKDIR% ^
        -p %SIM_HOST_PORT%:%SIM_CONTAINER_PORT% ^
        --no-healthcheck ^
        --restart unless-stopped ^
        %IMAGE% python -m recon_agent simulate --host 0.0.0.0 --port %SIM_CONTAINER_PORT%
)
if errorlevel 1 (
    echo [start_container] the simulator container did not start.
    exit /b 1
)
echo.
echo Source simulator: http://localhost:%SIM_HOST_PORT%/
echo It publishes to the event log on volume %SIM_VOLUME%. The worklist reads it when .env sets RECON_SOURCE=events.
echo Empty log? Use "Reset to starting day" in the simulator, or: start_container.bat events seed
goto :end

:sim_stop
docker stop %SIM_CONTAINER%
goto :end

:sim_remove
docker rm -f %SIM_CONTAINER%
goto :end

:sim_logs
docker logs -f %SIM_CONTAINER%
goto :end

:stop
echo Stopping %CONTAINER_NAME%...
docker stop %CONTAINER_NAME%
goto :end

:remove
echo Stopping and removing %CONTAINER_NAME%...
docker rm -f %CONTAINER_NAME%
goto :end

:logs
echo Tailing %CONTAINER_NAME% logs. Press Ctrl+C to stop.
docker logs -f %CONTAINER_NAME%
goto :end

:status
docker ps -a -f "name=^%CONTAINER_NAME%$"
goto :end

:usage
echo Usage:
echo   start_container.bat               Smart start (build if missing, create if missing, start if stopped), then open a shell.
echo   start_container.bat build         Force-rebuild image and re-create container, then open a shell.
echo   start_container.bat shell         Open a shell on the already-running container.
echo   start_container.bat run           Run the reconciliation once (writes reports to output\).
echo   start_container.bat stop          Stop the container (keep it for fast restart).
echo   start_container.bat remove        Stop AND remove the container.
echo   start_container.bat logs          Follow container logs.
echo   start_container.bat status        Show container state.
echo   start_container.bat events ARGS   Publish or inspect simulated events, e.g. "events seed", "events status".
echo   start_container.bat sim           Start the source simulator on http://localhost:8101/ (sim-stop, sim-remove, sim-logs).
goto :end

:end
endlocal
