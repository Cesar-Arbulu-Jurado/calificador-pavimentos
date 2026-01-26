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
    model = genai.GenerativeModel('gemini-2.5-flash')
    
    # Preparamos la imagen
    image_parts = [
        {
            "mime_type": image_file.type,
            "data": image_file.getvalue()
        }
    ]

    # PROMPT INTEGRADO: Rúbrica Pedagógica + Salida JSON
    prompt = f"""
    # SISTEMA DE EVALUACIÓN DE EXÁMENES MANUSCRITOS — INGENIERÍA CIVIL

    ## ROL
    Eres un evaluador académico experto en Ingeniería Civil, especializado en Pavimentos y Mecánica de Suelos, con amplia experiencia en programas de pregrado latinoamericanos. Evalúas con rigor técnico pero justicia pedagógica.

    ## CONTEXTO
    - Examen: Manuscrito (imagen adjunta)
    - Total de preguntas: {num_questions}
    - Escala: 0 a 5 puntos por pregunta (admite decimales con un decimal)
    - Puntaje máximo total: {num_questions * 5} puntos

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

    ## RESTRICCIONES
    - No inventes contenido que no esté visible en la imagen.
    - Ante ambigüedad caligráfica, aplica el principio de interpretación más favorable al alumno.
    - Distingue entre errores conceptuales (penalizan más) y errores menores.
    - Usa notación decimal con coma (ej.: 3.5).

    ## SALIDA REQUERIDA (SOLO JSON)
    Para garantizar la compatibilidad con el sistema, ignora el formato de reporte textual y DEVUELVE ESTRICTAMENTE UN JSON con esta estructura:
    {{
        "detalles": [
            {{
                "pregunta": 1, 
                "puntaje": 0.0, 
                "feedback": "Transcripción: [texto]... Análisis: [texto]... Retroalimentación: [texto]"
            }},
            {{
                "pregunta": 2, 
                "puntaje": 0.0, 
                "feedback": "..."
            }}
            ... (repetir para todas las preguntas)
        ],
        "comentario_final": "Resumen ejecutivo: Puntaje total, porcentaje y calificación cualitativa según la rúbrica."
    }}
    """
    
    # Configuración corregida y unificada
    generation_config = {
        "temperature": 0.1,
        "top_p": 0.95,
        "top_k": 40,
        "max_output_tokens": 8192,
        "response_mime_type": "application/json",
    }

    try:
        # Generamos el contenido
        response = model.generate_content(
            [prompt, image_parts[0]], 
            generation_config=generation_config
        )
        
        return json.loads(response.text)

    except Exception as e:
        st.error(f"Error interpretando la respuesta de la IA: {e}")
        return None

# --- GENERACIÓN DE PDF ---
def create_pdf(student_name, grading_data, total_score):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", size=12)
    
    pdf.cell(200, 10, txt=f"Resultados del Examen", ln=1, align='C')
    pdf.cell(200, 10, txt=f"Alumno(a): {student_name}", ln=1, align='L')
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

# --- LECTURA DE CONFIGURACIÓN (Solucionario + Contraseña) ---
try:
    # 1. Conectamos
    hoja_registro = connect_to_sheets()
    
    # 2. Buscamos la pestaña "Config"
    hoja_config = hoja_registro.spreadsheet.worksheet("Config")
    
    # 3. Leemos Solucionario (A1) y Contraseña (A2)
    answer_key = hoja_config.acell('A1').value
    exam_password_sheet = hoja_config.acell('A2').value # <--- NUEVO: Leemos la clave
    
    # Convertimos a texto por seguridad (por si en Excel pusiste solo números)
    exam_password_sheet = str(exam_password_sheet).strip() if exam_password_sheet else None

    # 4. Definimos preguntas
    num_questions = 4

    if not answer_key:
        st.error("⚠️ Error: Falta el solucionario en la celda A1 de 'Config'.")
        st.stop()
        
except Exception as e:
    st.error(f"⚠️ Error de conexión con Google Sheets: {e}")
    st.stop()

# --- ZONA DE ACCESO ---
st.title("📝 Control de lectura")

# 1. PANTALLA DE BLOQUEO
input_code = st.text_input("🔐 Ingresa el CÓDIGO DE EXAMEN proporcionado por el profesor:", type="password")

# Verificamos si el código coincide (o si la celda A2 está vacía, dejamos pasar)
if exam_password_sheet and input_code != exam_password_sheet:
    st.info("👋 Por favor ingresa el código correcto para desbloquear el examen.")
    st.stop() # DETIENE LA APP AQUÍ si la clave no es correcta

# --- ZONA DEL ALUMNO (Solo visible si el código es correcto) ---
st.success("✅ Acceso autorizado")
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
                
                # Ajuste de escala (si son 5 preguntas = 25 pts, escalamos a 20)
                # Si son 4 preguntas = 20 pts, se queda igual.
                if num_questions * 5 != 20:
                     total_score = (total_score / (num_questions * 5)) * 20
                
                total_score = round(total_score, 2)

                # 2. Guardar en Sheets
                try:
                    sheet = connect_to_sheets()
                    # Guardamos: Nombre, Fecha, Nota, Código usado (para auditoría)
                    sheet.append_row([
                        name, 
                        datetime.now().strftime("%Y-%m-%d %H:%M"), 
                        total_score
                    ])
                    st.toast("✅ Nota guardada en el registro.")
                except Exception as e:
                    st.error(f"Error guardando en Sheets: {e}")

                # 3. Mostrar resultados y PDF
                st.success(f"Examen calificado. Tu nota es: **{total_score}/20**")
                
                pdf_bytes = create_pdf(name, result, total_score)
                st.download_button(
                    label="📄 Descargar Feedback en PDF",
                    data=pdf_bytes,
                    file_name=f"Feedback_{name.replace(' ', '_')}.pdf",
                    mime="application/pdf"
                )