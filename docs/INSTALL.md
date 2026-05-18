# DataLens — Installation Guide

## Windows

1. Install Python 3.10+ from python.org
   Make sure to check "Add Python to PATH"
2. Double-click `run_datalense.bat`
3. DataLens opens in your browser
4. Press Ctrl+C in the console to stop

## Mac / Linux

1. Install Python 3.10+
2. Open Terminal in the DataLens folder
3. Run: `bash run_datalense.sh`
4. DataLens opens in your browser
5. Press Ctrl+C to stop

## System Requirements

- Python 3.10 or higher
- 4 GB RAM minimum
- 8 GB RAM recommended for files over 1 GB
- Windows 10/11, macOS 12+, Ubuntu 20+
- 500 MB disk space for dependencies

## Troubleshooting

- **"Python not found"**: reinstall Python and check "Add Python to PATH"
- **Port 8000 in use**: close other apps using port 8000 and try again
- **Slow first launch**: normal — pip is installing dependencies once only
