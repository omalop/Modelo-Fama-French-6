# Despliegue del Sistema Cuantitativo y Configuración de IA

Este documento detalla los pasos para convertir el proyecto local en una aplicación web accesible mediante Firebase y conectar un asistente de IA fundamentado en los cuadernos del proyecto.

## 1. Despliegue en Firebase Hosting

### Paso 1: Inicialización de Proyecto Firebase
1. Instalar Firebase Tools: `npm install -g firebase-tools`
2. Iniciar sesión: `firebase login`
3. Inicializar el proyecto en la raíz: `firebase init hosting`
   - Carpeta pública: `public`
   - Configurar como SPA: `No`.
4. Crear la estructura inicial: `mkdir public`

### Paso 2: Generación del Build (Dashboard)
1. Ejecutar el orquestador de datos: `python src/models/allocation_tres_pilares.py`
2. Generar el Dashboard final interactivo: `python src/utils/generar_dashboard.py`
3. Copiar el archivo generado a la carpeta de despliegue: `cp data/processed/Dashboard_Final.html public/index.html`

### Paso 3: Despliegue Web
1. Desplegar el sitio: `firebase deploy --only hosting`
2. El sistema estará disponible en `https://[TU-PROJECTO].web.app`

## 2. Integración de IA Asesora (Asistente Científico)

Para conectar la IA con los fundamentos científicos (Artículos 2 y 8 de la Constitución Antigravity):

### Fuente de Conocimiento (RAG)
El asistente de IA debe utilizar como fuente de conocimiento obligatoria:
- `docs/metodologia.md`
- `docs/README_QUANTAMENTAL.txt`
- El historial de transacciones del **Diario de Operaciones**.

### Configuración del Prompt
El prompt del sistema debe ser el siguiente:
> "Eres un consultor financiero cuantitativo basado estrictamente en el Modelo Fama-French 6 y la Metodología Domenec detallada en la documentación adjunta. Tus recomendaciones deben citar fuentes académicas y validar supuestos estadísticos. El usuario es un inversor avanzado que opera en el mercado argentino (Bonos, CEDEARs, Acciones)."

## 3. Optimización SEO (Prueba de CEO/SEO)

Para maximizar la visibilidad y profesionalismo del sitio:
- **Title Tag**: `Terminal Cuantitativo AR - Modelo Fama-French 6 & Análisis de Bonos`
- **Meta Description**: `Sistema profesional de optimización de carteras para bonos soberanos argentinos, corporativos y carry trade basado en modelos multifactoriales de Fama-French.`
- **Semantic HTML**: Asegurar el uso de un único `<h1>` por página y etiquetas descriptivas para los gráficos dinámicos.
