import streamlit as st
import google.generativeai as genai
import gspread
from google.oauth2.service_account import Credentials
from fpdf import FPDF
import json
import pandas as pd
from datetime import datetime

# AGREGA ESTA LÍNEA CON EL ID QUE COPIASTE EN EL PASO 1
SHEET_ID = "1LoByskK71512Gfyekk67k_OuXIbAg5BkBxq7Jcermz0"

# NUEVA CONFIGURACIÓN DE GEMINI (Usando la llave oculta en Secrets)
try:
    # Busca la llave "GEMINI_KEY" en la caja fuerte de Streamlit
    genai.configure(api_key=st.secrets["GEMINI_KEY"])
except:
    st.error("No encontré la llave GEMINI_KEY en los secretos.")

# Configurar Google Sheets (Compatible con PC y Nube)
def connect_to_sheets():
    scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    
    # 1. Si estamos en la nube (Streamlit Cloud), usa los "Secretos"
    if "gcp_service_account" in st.secrets:
        creds = Credentials.from_service_account_info(
            st.secrets["gcp_service_account"],
            scopes=scopes
        )
    # 2. Si estamos en tu PC, usa el archivo normal
    else:
        creds = Credentials.from_service_account_file("credentials.json", scopes=scopes)
        
    client = gspread.authorize(creds)
    
    # Usamos la variable global SHEET_ID que definiste arriba
    sheet = client.open_by_key(SHEET_ID).sheet1 
    return sheet

# --- LÓGICA DE IA ---
def grade_exam_with_gemini(image_file, answer_key, num_questions):
    # CAMBIO IMPORTANTE: Usamos 'gemini-2.5-pro' porque es el que soporta mejor el modo JSON y es más estable.
    model = genai.GenerativeModel('gemini-2.5-flash')
    
    # Preparamos la imagen
    image_parts = [
        {
            "mime_type": image_file.type,
            "data": image_file.getvalue()
        }
    ]

    prompt = f"""
# SISTEMA DE EVALUACIÓN DE EXÁMENES MANUSCRITOS — INGENIERÍA CIVIL

## ROL
Eres un evaluador académico experto en Ingeniería Civil, especializado en Pavimentos y Mecánica de Suelos, con amplia experiencia en programas de pregrado latinoamericanos. Evalúas con rigor técnico pero justicia pedagógica.

## CONTEXTO
- Examen: Manuscrito (imagen adjunta)
- Total de preguntas: {num_questions}
- Escala: 0 a 5 puntos por pregunta (admite decimales con un decimal)
- Puntaje máximo total: {num_questions × 5} puntos

## SOLUCIONARIO DE REFERENCIA
{answer_key}

## PROTOCOLO DE EVALUACIÓN

### Paso 1: Transcripción
Transcribe literalmente cada respuesta del alumno. Si la caligrafía es parcialmente ilegible:
- Indica los fragmentos dudosos entre corchetes: [texto incierto]
- Si es completamente ilegible, registra: [ILEGIBLE]

### Paso 2: Criterios de puntuación
| Puntaje | Criterio |
|---------|----------|
| 5,0 | Respuesta correcta, completa y bien fundamentada |
| 4,0–4,9 | Correcta con omisiones menores o imprecisiones de forma |
| 3,0–3,9 | Concepto central correcto pero con errores parciales o desarrollo incompleto |
| 2,0–2,9 | Comprensión parcial con errores conceptuales significativos |
| 1,0–1,9 | Intento con algún elemento rescatable pero fundamentalmente incorrecto |
| 0,0–0,9 | Incorrecta, en blanco, o completamente ilegible |

### Paso 3: Evaluación por pregunta
Para cada pregunta, aplica el siguiente análisis:
1. **Identificación de conceptos clave** requeridos según el solucionario
2. **Verificación de presencia** de dichos conceptos en la respuesta
3. **Detección de errores** conceptuales, de cálculo o de procedimiento
4. **Valoración de la argumentación** técnica (si aplica)

## FORMATO DE SALIDA

### Resumen ejecutivo
- **Alumno**: [si es identificable en la imagen]
- **Puntaje total**: X,X / {puntaje_máximo}
- **Porcentaje**: XX,X %
- **Calificación cualitativa**: [Deficiente / Regular / Bueno / Muy bueno / Excelente]

### Detalle por pregunta

**Pregunta 1** — Puntaje: X,X / 5,0
- *Transcripción*: [respuesta del alumno]
- *Aciertos*: [elementos correctos identificados]
- *Errores*: [errores detectados]
- *Retroalimentación*: [recomendación específica y constructiva]

[Repetir para cada pregunta]

### Observaciones generales
[Comentario global sobre fortalezas, debilidades recurrentes y recomendaciones de estudio]

## RESTRICCIONES
- No inventes contenido que no esté visible en la imagen
- Ante ambigüedad caligráfica, aplica el principio de interpretación más favorable al alumno si existe una lectura razonable que sea correcta
- Distingue entre errores conceptuales (penalizan más) y errores de transcripción o cálculo menor
- Usa notación decimal con coma (ej.: 3,5 en lugar de 3.5)    

    SALIDA REQUERIDA (SOLO JSON):
    Devuelve estrictamente un objeto JSON con la siguiente estructura, sin texto adicional:
    {{
        "detalles": [
            {{"pregunta": 1, "puntaje": 0.0, "feedback": "texto..."}},
            {{"pregunta": 2, "puntaje": 0.0, "feedback": "texto..."}}
            ...
        ],
        "comentario_final": "Un consejo general para el alumno..."
    }}
    """
    
    # Configuración corregida y unificada
    generation_config = {
        "temperature": 0.1,
        "top_p": 0.95,
        "top_k": 40,
        "max_output_tokens": 8192,                # Suficiente espacio para que no se corte
        "response_mime_type": "application/json", # Obliga a la IA a responder en JSON perfecto
    }

    try:
        # Generamos el contenido
        response = model.generate_content(
            [prompt, image_parts[0]], 
            generation_config=generation_config
        )
        
        # Al usar response_mime_type, la respuesta ya es un JSON válido.
        # No hace falta limpiar "```json" porque la IA ya no lo pone en este modo.
        return json.loads(response.text)

    except Exception as e:
        st.error(f"Error interpretando la respuesta de la IA: {e}")
        return None

# --- GENERACIÓN DE PDF ---
def create_pdf(student_name, grading_data, total_score):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", size=12)
    
    pdf.cell(200, 10, txt=f"Resultados Examen de Pavimentos", ln=1, align='C')
    pdf.cell(200, 10, txt=f"Alumno: {student_name}", ln=1, align='L')
    pdf.cell(200, 10, txt=f"Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M')}", ln=1, align='L')
    pdf.line(10, 35, 200, 35)
    pdf.ln(10)
    
    for item in grading_data['detalles']:
        pdf.set_font("Arial", 'B', 12)
        pdf.cell(0, 10, txt=f"Pregunta {item['pregunta']} - Puntaje: {item['puntaje']}/5", ln=1)
        pdf.set_font("Arial", size=11)
        pdf.multi_cell(0, 10, txt=f"Feedback: {item['feedback']}")
        pdf.ln(2)
        
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
    pdf.set_font("Arial", 'B', 14)
    pdf.cell(0, 15, txt=f"NOTA FINAL: {total_score} / 20", ln=1, align='R')
    
    pdf.set_font("Arial", 'I', 11)
    pdf.multi_cell(0, 10, txt=f"Recomendación General: {grading_data['comentario_final']}")
    
    return pdf.output(dest='S').encode('latin-1')

# --- INTERFAZ DE USUARIO (STREAMLIT) ---
st.set_page_config(page_title="Examen Pavimentos", page_icon="📝")

# --- LECTURA AUTOMÁTICA DEL SOLUCIONARIO ---
try:
    # 1. Conectamos (esto nos trae la Hoja 1 por defecto)
    hoja_registro = connect_to_sheets()
    
    # 2. "Saltamos" al archivo completo para buscar la pestaña "Config"
    # IMPORTANTE: Tu pestaña en Google Sheets debe llamarse exactamente Config
    hoja_config = hoja_registro.spreadsheet.worksheet("Config")
    
    # 3. Leemos la celda A1
    answer_key = hoja_config.acell('A1').value
    
    # 4. Definimos las preguntas (puedes cambiar este número aquí si necesitas)
    num_questions = 4

    if not answer_key:
        st.error("⚠️ Error: La celda A1 de la pestaña 'Config' está vacía.")
        st.stop()
        
except Exception as e:
    st.error(f"⚠️ No pude leer el solucionario. Asegúrate de tener una pestaña llamada 'Config' y el texto en A1. Error: {e}")
    st.stop()

# --- ZONA DEL ALUMNO ---
st.title("📝 Control de lectura")
st.markdown("Sube una foto clara de tu hoja de respuestas.")

name = st.text_input("Apellidos y Nombres completas")
uploaded_file = st.file_uploader("Tomar foto o subir archivo", type=['jpg', 'png', 'jpeg'])

if st.button("Enviar y Calificar"):
    if not name or not uploaded_file:
        st.warning("Por favor ingresa tu nombre y sube una foto.")
    elif not answer_key:
        st.error("El profesor aún no ha cargado el solucionario.")
    else:
        with st.spinner('Analizando manuscrito y calificando con IA...'):
            # 1. Calificar
            result = grade_exam_with_gemini(uploaded_file, answer_key, num_questions)
            
            if result:
                # Calcular nota final
                total_score = sum(item['puntaje'] for item in result['detalles'])
                
                # Ajuste si son 4 preguntas (4x5=20) o 5 preguntas (5x4=20)
                # La IA puntúa sobre 5. Si son 5 preguntas, la suma es 25. Hay que escalar a 20.
                # Si son 4 preguntas, la suma es 20. No hay que escalar.
                if num_questions == 5:
                    total_score = (total_score / 25) * 20
                
                total_score = round(total_score, 2)

                # 2. Guardar en Sheets
                try:
                    sheet = connect_to_sheets()
                    sheet.append_row([name, datetime.now().strftime("%Y-%m-%d %H:%M"), total_score])
                    st.toast("✅ Nota guardada en el registro.")
                except Exception as e:
                    st.error(f"Error guardando en Sheets (Avise al profesor): {e}")

                # 3. Mostrar resultados y PDF
                st.success(f"Examen calificado. Tu nota es: **{total_score}/20**")
                
                pdf_bytes = create_pdf(name, result, total_score)
                st.download_button(
                    label="📄 Descargar Feedback en PDF",
                    data=pdf_bytes,
                    file_name=f"Feedback_{name.replace(' ', '_')}.pdf",
                    mime="application/pdf"
                )