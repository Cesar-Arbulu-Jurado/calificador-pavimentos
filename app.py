import streamlit as st
import google.generativeai as genai
import gspread
from google.oauth2.service_account import Credentials
from fpdf import FPDF
import json
import pandas as pd
from datetime import datetime
import time
import random
import pytz
from google.api_core import exceptions
import smtplib # <--- NUEVO: Para enviar correos
from email.mime.text import MIMEText # <--- NUEVO
from email.mime.multipart import MIMEMultipart # <--- NUEVO
from email.mime.application import MIMEApplication # <--- NUEVO

# --- CONFIGURACIÓN ---
SHEET_ID = "1LoByskK71512Gfyekk67k_OuXIbAg5BkBxq7Jcermz0"

# Configuración de Gemini
try:
    if "GEMINI_KEY" in st.secrets:
        genai.configure(api_key=st.secrets["GEMINI_KEY"])
    else:
        st.error("⚠️ Error: No encontré la llave GEMINI_KEY en los secretos.")
except Exception as e:
    st.error(f"Error configurando API Key: {e}")

# --- FUNCIÓN DE HORA LOCAL (PERÚ) ---
def get_current_time_peru():
    peru_tz = pytz.timezone('America/Lima')
    return datetime.now(peru_tz).strftime("%Y-%m-%d %H:%M")

# --- FUNCIÓN DE CONEXIÓN A SHEETS ---
@st.cache_resource(ttl=3600)  
def connect_to_sheets():
    scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    
    if "gcp_service_account" in st.secrets:
        creds = Credentials.from_service_account_info(
            st.secrets["gcp_service_account"],
            scopes=scopes
        )
    else:
        try:
            creds = Credentials.from_service_account_file("credentials.json", scopes=scopes)
        except FileNotFoundError:
            st.error("No se encontraron credenciales.")
            st.stop()
            
    client = gspread.authorize(creds)
    return client.open_by_key(SHEET_ID)

# --- FUNCIÓN CARGAR CONFIGURACIÓN ---
@st.cache_data(ttl=600) 
def load_config_data():
    try:
        wb = connect_to_sheets()
        hoja_config = wb.worksheet("Config")
        data_config = hoja_config.batch_get(['A1', 'A2'])
        
        answer_key = data_config[0][0][0] if data_config[0] else None
        exam_password = data_config[1][0][0] if (len(data_config) > 1 and data_config[1]) else None
        
        if exam_password:
            exam_password = str(exam_password).strip()
            
        return answer_key, exam_password
    except Exception as e:
        return None, None

# --- FUNCIÓN VERIFICAR ALUMNO ---
def check_if_student_exists(dni):
    try:
        wb = connect_to_sheets()
        sheet = wb.sheet1
        records = sheet.get_all_values()
        for row in records:
            if len(row) >= 4 and str(row[0]).strip().upper() == str(dni).strip().upper():
                return True, row[3]
        return False, None
    except Exception as e:
        print(f"Error leyendo duplicados: {e}")
        return False, None

# --- NUEVA FUNCIÓN: ENVIAR CORREO CON PDF ---
def send_email_with_pdf(recipient_email, student_name, pdf_bytes):
    # Verificar si existen secretos configurados
    if "smtp" not in st.secrets:
        st.warning("⚠️ No se configuró el servidor de correo (secrets). El PDF no se envió por email.")
        return False

    smtp_user = st.secrets["smtp"]["EMAIL"]
    smtp_password = st.secrets["smtp"]["PASSWORD"]
    smtp_server = st.secrets["smtp"].get("SERVER", "smtp.gmail.com")
    smtp_port = st.secrets["smtp"].get("PORT", 465)

    msg = MIMEMultipart()
    msg['Subject'] = f"Resultado Evaluación - {student_name}"
    msg['From'] = f"Evaluación Automática <{smtp_user}>"
    msg['To'] = recipient_email

    body = f"""Saludos {student_name},

Adjunto encontrará el informe detallado de su evaluación.
Fecha de generación: {get_current_time_peru()}

Atentamente,
Mgt. César Arbulú Jurado - Docente
"""
    msg.attach(MIMEText(body, 'plain'))

    # Adjuntar PDF
    pdf_attachment = MIMEApplication(pdf_bytes, Name=f"Informe_{student_name}.pdf")
    pdf_attachment['Content-Disposition'] = f'attachment; filename="Informe_{student_name}.pdf"'
    msg.attach(pdf_attachment)

    try:
        # Conexión segura SSL
        with smtplib.SMTP_SSL(smtp_server, smtp_port) as smtp:
            smtp.login(smtp_user, smtp_password)
            smtp.send_message(msg)
        return True
    except Exception as e:
        st.error(f"Error enviando correo: {e}")
        return False

# --- LÓGICA DE IA ---
def grade_exam_with_gemini(image_file, answer_key, num_questions):
    model_name = 'gemini-2.0-flash-lite-001' 
    model = genai.GenerativeModel(model_name)
    
    image_parts = [
        {"mime_type": image_file.type, "data": image_file.getvalue()}
    ]

    prompt = f"""
    # SISTEMA DE EVALUACIÓN DE EXÁMENES MANUSCRITOS — INGENIERÍA CIVIL

    ## ROL
    Eres un docente evaluador académico experto en Ingeniería Civil, especializado en Diseño de Pavimentos, Diseño de Cimentaciones y Mecánica de Suelos, con amplia experiencia en programas de pregrado latinoamericanos.
    Evalúas con rigor técnico pero justicia pedagógica.

    ## CONTEXTO
    - Examen: Manuscrito (imagen adjunta)
    - Total de preguntas: {num_questions}
    - Escala: 0 a 5 puntos por pregunta (admite decimales con un decimal)
    - Puntaje máximo total: {num_questions * 5} puntos

    ## SOLUCIONARIO DE REFERENCIA
    {answer_key}

    ## PROTOCOLO DE EVALUACIÓN

    ### Paso 1: Transcripción
    Transcribe literalmente cada respuesta del alumno.
    Si la caligrafía es parcialmente ilegible:
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
    - No inventes contenido que no esté visible en la imagen
    - Ante ambigüedad caligráfica, aplica el principio de interpretación más favorable al alumno si existe una lectura razonable que sea correcta
    - Distingue entre errores conceptuales (penalizan más) y errores de transcripción o cálculo menor
    - Usa notación decimal con coma (ej.: 3,5 en lugar de 3.5)

    ## ADAPTACIÓN TÉCNICA (FORMATO JSON OBLIGATORIO)
    Aunque tu rol es generar un reporte académico, el sistema informático requiere procesar los datos estructurados.
    Por lo tanto, traduce tu evaluación pedagógica al siguiente formato JSON estricto:

    {{
        "detalles": [
            {{
                "pregunta": 1, 
                "puntaje": 0.0, 
                "feedback": "INCLUYE AQUÍ: Transcripción, Aciertos, Errores y Retroalimentación detallada según el Paso 3."
            }},
            ... (repetir para todas las preguntas)
        ],
        "comentario_final": "INCLUYE AQUÍ: El Resumen Ejecutivo (Puntaje total, Porcentaje, Calificación cualitativa) y las Observaciones generales."
    }}
    """
    
    generation_config = {
        "temperature": 0.1,
        "max_output_tokens": 8192,
        "response_mime_type": "application/json",
    }

    max_retries = 3
    base_delay = 2 
    
    time.sleep(random.uniform(0.1, 4.0)) 

    for attempt in range(max_retries):
        try:
            response = model.generate_content(
                [prompt, image_parts[0]], 
                generation_config=generation_config
            )
            return json.loads(response.text)

        except exceptions.ResourceExhausted:
            wait_time = base_delay * (2 ** attempt) + random.uniform(0, 1)
            st.toast(f"⏳ Tráfico alto. Reintentando en {int(wait_time)}s... (Intento {attempt+1}/{max_retries})")
            time.sleep(wait_time)
            
        except Exception as e:
            st.error(f"Error técnico: {e}")
            return None
            
    st.error("❌ El sistema está saturado. Por favor intenta enviar de nuevo en 1 minuto.")
    return None

# --- GENERACIÓN DE PDF ---
def create_pdf(student_name, dni, grading_data, total_score):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", size=12)
    
    # 1. ENCABEZADO INSTITUCIONAL
    pdf.set_font("Arial", 'B', 12)
    pdf.cell(0, 6, txt="Universidad Nacional de San Antonio Abad del Cusco", ln=1, align='C')
    pdf.set_font("Arial", size=12)
    pdf.cell(0, 6, txt="Escuela Profesional de Ingeniería Civil", ln=1, align='C')
    pdf.cell(0, 6, txt="Docente: Mgt. César Arbulú Jurado", ln=1, align='C')
    
    pdf.ln(5)

    # 2. DATOS DEL EXAMEN
    pdf.set_font("Arial", 'B', 14)
    pdf.cell(0, 10, txt=f"Resultados del Control de Lectura", ln=1, align='C')
    pdf.set_font("Arial", size=12)
    
    pdf.cell(0, 8, txt=f"Alumno: {student_name}", ln=1, align='L')
    pdf.cell(0, 8, txt=f"DNI/Código: {dni}", ln=1, align='L')
    pdf.cell(0, 8, txt=f"Fecha: {get_current_time_peru()}", ln=1, align='L')
    
    # 3. LÍNEA SEPARADORA
    pdf.ln(2)
    y_position = pdf.get_y()
    pdf.line(10, y_position, 200, y_position)
    pdf.ln(10)
    
    # 4. CUERPO
    for item in grading_data['detalles']:
        pdf.set_font("Arial", 'B', 12)
        pdf.cell(0, 10, txt=f"Pregunta {item['pregunta']} - Puntaje: {item['puntaje']}/5", ln=1)
        pdf.set_font("Arial", size=11)
        pdf.multi_cell(0, 6, txt=f"{item['feedback']}")
        pdf.ln(3)
        
    # 5. NOTA FINAL
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
    pdf.ln(5)
    pdf.set_font("Arial", 'B', 14)
    pdf.cell(0, 10, txt=f"NOTA FINAL: {total_score} / 20", ln=1, align='R')
    
    pdf.set_font("Arial", 'I', 11)
    pdf.multi_cell(0, 6, txt=f"Evaluación Global:\n{grading_data['comentario_final']}")
    
    return pdf.output(dest='S').encode('latin-1')

# --- INTERFAZ PRINCIPAL ---
st.set_page_config(page_title="Control de lectura", page_icon="📝")

# 1. CARGA DE CONFIGURACIÓN
answer_key, exam_password_sheet = load_config_data()
num_questions = 4 

if not answer_key:
    if st.button("🔄 Recargar Configuración"):
        st.cache_data.clear()
        st.rerun()
    st.error("⚠️ Error cargando la configuración. Si persiste, contacte al profesor.")
    st.stop()

# 2. PANTALLA DE BLOQUEO
st.title("📝 Control de lectura")

if exam_password_sheet:
    input_code = st.text_input("🔐 Ingresa el CÓDIGO DE ACCESO:", type="password")
    
    if input_code != exam_password_sheet:
        st.info("Ingresa el código proporcionado por el profesor.")
        st.stop() 
    else:
        st.success("Acceso Autorizado ✅")

# 3. ZONA DEL ALUMNO
st.markdown("---")
st.write("Ingresa tus datos y sube la foto de tu examen.")

# --- CAMBIO DE DISEÑO: EMAIL AL LADO DEL DNI ---
col_dni, col_email = st.columns(2)
with col_dni:
    dni = st.text_input("DNI / Código de Estudiante")
with col_email:
    email_alumno = st.text_input("Correo Electrónico (para enviar resultados)")

name = st.text_input("Apellidos y Nombres completos")

uploaded_file = st.file_uploader("Tomar foto o subir archivo", type=['jpg', 'png', 'jpeg'])

if st.button("Enviar y Calificar"):
    # Validamos que el email también esté presente
    if not dni or not name or not email_alumno or not uploaded_file:
        st.warning("⚠️ Faltan datos: Asegúrate de completar DNI, Email, Nombre y Foto.")
    else:
        # VALIDACIÓN 1: Verificar duplicados
        with st.spinner('Verificando registro...'):
            ya_existe, nota_existente = check_if_student_exists(dni)
            
            if ya_existe:
                st.warning(f"⛔ El DNI {dni} ya realizó este examen previamente.")
                st.info(f"📋 Tu nota registrada es: **{nota_existente} / 20**")
                st.error("El sistema no admite reenvíos.")
                st.stop() 

        # VALIDACIÓN 2: Calificación con IA
        with st.spinner('Evaluando con criterio pedagógico...'):
            result = grade_exam_with_gemini(uploaded_file, answer_key, num_questions)
            
            if result:
                # Cálculo de Nota
                try:
                    puntos = sum(item['puntaje'] for item in result['detalles'])
                    nota_final = round((puntos / (num_questions * 5)) * 20, 2)
                except:
                    nota_final = 0.0

                # Guardado en Sheets
                try:
                    wb = connect_to_sheets()
                    hoja_registro = wb.sheet1
                    hoja_registro.append_row([
                        str(dni).strip(),
                        name, 
                        get_current_time_peru(),
                        nota_final,
                        email_alumno # Guardamos también el correo en el Excel
                    ])
                    st.toast("✅ Nota registrada correctamente.")
                except Exception as e:
                    st.error(f"Error guardando registro: {e}")

                # Generar PDF
                pdf_bytes = create_pdf(name, dni, result, nota_final)

                # Resultados
                st.balloons()
                st.success(f"CALIFICACIÓN COMPLETADA: **{nota_final} / 20**")

                # INTENTO DE ENVÍO DE CORREO
                with st.spinner('Enviando copia a tu correo...'):
                    email_enviado = send_email_with_pdf(email_alumno, name, pdf_bytes)
                    if email_enviado:
                        st.success(f"📧 Se envió una copia del informe a {email_alumno}")
                    else:
                        st.warning("No se pudo enviar el correo automático, pero puedes descargar el PDF abajo.")
                
                # Botón de Descarga Manual (Backup)
                st.download_button(
                    label="⬇️ Descargar Informe Pedagógico (PDF)",
                    data=pdf_bytes,
                    file_name=f"Informe_{dni}.pdf",
                    mime="application/pdf"
                )