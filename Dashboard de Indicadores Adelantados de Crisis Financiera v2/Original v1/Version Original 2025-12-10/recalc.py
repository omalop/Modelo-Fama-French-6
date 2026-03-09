#!/usr/bin/env python3
"""
Script para recalcular fórmulas de Excel usando LibreOffice.
Uso: python recalc.py archivo.xlsx [timeout_segundos]
"""
import sys
import subprocess
import json
from pathlib import Path

def recalc_formulas(file_path, timeout=30):
    """Recalcula formulas usando LibreOffice."""
    file_path = Path(file_path).absolute()
    
    if not file_path.exists():
        return {'status': 'error', 'message': f'File not found: {file_path}'}
    
    # Comando de LibreOffice para recalcular
    cmd = [
        'libreoffice', '--headless', '--invisible',
        '--convert-to', 'xlsx',
        '--outdir', str(file_path.parent),
        str(file_path)
    ]
    
    try:
        result = subprocess.run(cmd, timeout=timeout, capture_output=True, text=True)
        if result.returncode == 0:
            return {'status': 'success', 'message': 'Formulas recalculated'}
        else:
            return {'status': 'error', 'message': result.stderr}
    except subprocess.TimeoutExpired:
        return {'status': 'error', 'message': f'Timeout after {timeout}s'}
    except Exception as e:
        return {'status': 'error', 'message': str(e)}

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python recalc.py <excel_file> [timeout]")
        sys.exit(1)
    
    file_path = sys.argv[1]
    timeout = int(sys.argv[2]) if len(sys.argv) > 2 else 30
    
    result = recalc_formulas(file_path, timeout)
    print(json.dumps(result, indent=2))
