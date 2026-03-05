#!/usr/bin/env python3
"""
Script auxiliar para configurar tu API Key de FRED fácilmente.
Ejecuta este script una sola vez para configurar todo.
"""

import os
from pathlib import Path

print("="*70)
print("  CONFIGURADOR DE API KEY - Crisis Dashboard")
print("="*70)
print()

# Solicitar la API key
print("📝 Ingresa tu FRED API Key")
print("   (Si no la tienes, obtén una gratis en: https://fred.stlouisfed.org/docs/api/api_key.html)")
print()

api_key = input("API Key: ").strip()

if not api_key:
    print("❌ No ingresaste ninguna clave. Saliendo...")
    exit(1)

# Opción 1: Crear archivo .env
print("\n🔧 Creando archivo .env...")
env_path = Path('.env')

try:
    with open(env_path, 'w') as f:
        f.write(f"FRED_API_KEY={api_key}\n")
    print(f"✅ Archivo .env creado exitosamente en: {env_path.absolute()}")
except Exception as e:
    print(f"⚠️  Error al crear .env: {e}")
    
    # Fallback: Crear FRED_API_KEY.env
    print("\n🔧 Intentando crear FRED_API_KEY.env...")
    env_path = Path('FRED_API_KEY.env')
    try:
        with open(env_path, 'w') as f:
            f.write(f"FRED_API_KEY={api_key}\n")
        print(f"✅ Archivo FRED_API_KEY.env creado en: {env_path.absolute()}")
    except Exception as e2:
        print(f"❌ Error al crear archivo: {e2}")
        exit(1)

# Verificar que se pueda leer
print("\n🧪 Verificando configuración...")
try:
    if Path('.env').exists():
        test_path = '.env'
    elif Path('FRED_API_KEY.env').exists():
        test_path = 'FRED_API_KEY.env'
    else:
        raise FileNotFoundError("No se encontró archivo de configuración")
    
    with open(test_path, 'r') as f:
        content = f.read()
        if api_key in content:
            print("✅ Configuración verificada correctamente")
        else:
            print("⚠️  Advertencia: La clave guardada no coincide")
            
except Exception as e:
    print(f"⚠️  No se pudo verificar: {e}")

print("\n" + "="*70)
print("✅ CONFIGURACIÓN COMPLETADA")
print("="*70)
print()
print("Ahora puedes ejecutar el dashboard con:")
print("   python crisis_dashboard_pro.py")
print()

# Opcional: Instalar python-dotenv si no está
try:
    import dotenv
    print("✅ python-dotenv ya está instalado")
except ImportError:
    print("⚠️  python-dotenv no está instalado (opcional pero recomendado)")
    print("   Para instalarlo: pip install python-dotenv")

print("\n¡Listo para usar! 🚀")
