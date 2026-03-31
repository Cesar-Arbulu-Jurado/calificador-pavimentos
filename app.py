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
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
import os
import urllib.request

# --- CONFIGURACIÓN ---
SHEET_ID = "1LoByskK71512Gfyekk67k_OuXIbAg5BkBxq7Jcermz0"

try:
    if "GEMINI_KEY" in st.secrets:
        genai.configure(api_key=st.secrets["GEMINI_KEY"])
    else:
        st.error("⚠️ Error: No encontré la llave GEMINI_KEY en los secretos.")
except Exception as e:
    st.error(f"Error configurando API Key: {e}")

SMTP_MAP = {
    "cesar.arbulu@unsaac.edu.pe": "smtp_unsaac",
    "carbuluj@uandina.edu.pe": "smtp_uandina",
}

def get_current_time_peru():
    peru_tz = pytz.timezone('America/Lima')
    return datetime.now(peru_tz).strftime("%Y-%m-%d %H:%M")

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

@st.cache_data(ttl=600) 
def load_config_data():
    try:
        wb = connect_to_sheets()
        hoja_config = wb.worksheet("Config")
        data_config = hoja_config.batch_get(['A1', 'A2', 'A3'])
        
        answer_key = data_config[0][0][0] if data_config[0] else None
        exam_password = data_config[1][0][0] if (len(data_config) > 1 and data_config[1]) else None
        sender_email = data_config[2][0][0] if (len(data_config) > 2 and data_config[2]) else None
        
        if exam_password:
            exam_password = str(exam_password).strip()
        if sender_email:
            sender_email = str(sender_email).strip()
            
        return answer_key, exam_password, sender_email
    except Exception as e:
        return None, None, None

def check_if_student_exists(codigo):
    try:
        wb = connect_to_sheets()
        sheet = wb.sheet1
        records = sheet.get_all_values()
        for row in records:
            if len(row) >= 4 and str(row[0]).strip().upper() == str(codigo).strip().upper():
                return True, row[3]
        return False, None
    except Exception as e:
        print(f"Error leyendo duplicados: {e}")
        return False, None

def get_smtp_credentials(sender_email):
    secret_key = SMTP_MAP.get(sender_email)
    
    if secret_key and secret_key in st.secrets:
        section = st.secrets[secret_key]
        return {
            "email": section["EMAIL"],
            "password": section["PASSWORD"],
            "server": section.get("SERVER", "smtp.gmail.com"),
            "port": section.get("PORT", 465),
        }
    
    if "smtp" in st.secrets:
        section = st.secrets["smtp"]
        return {
            "email": section["EMAIL"],
            "password": section["PASSWORD"],
            "server": section.get("SERVER", "smtp.gmail.com"),
            "port": section.get("PORT", 465),
        }
    
    return None

def send_email_with_pdf(recipient_email, student_name, pdf_bytes, sender_email_config=None):
    creds = get_smtp_credentials(sender_email_config)
    
    if not creds:
        st.warning("⚠️ No se configuró el servidor de correo (secrets). El PDF no se envió por email.")
        return False

    msg = MIMEMultipart()
    msg['Subject'] = f"Resultado Evaluación - {student_name}"
    msg['From'] = f"Evaluación Automática <{creds['email']}>"
    msg['To'] = recipient_email

    body = f"""Saludos {student_name},

Adjunto encontrará el informe detallado de su evaluación.
Fecha de generación: {get_current_time_peru()}

Atentamente,
Mgt. César Arbulú Jurado - Docente
"""
    msg.attach(MIMEText(body, 'plain'))

    pdf_attachment = MIMEApplication(pdf_bytes, Name=f"Informe_{student_name}.pdf")
    pdf_attachment['Content-Disposition'] = f'attachment; filename="Informe_{student_name}.pdf"'
    msg.attach(pdf_attachment)

    try:
        with smtplib.SMTP_SSL(creds['server'], creds['port']) as smtp:
            smtp.login(creds['email'], creds['password'])
            smtp.send_message(msg)
        return True
    except Exception as e:
        st.error(f"Error enviando correo: {e}")
        return False

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

# --- DESCARGA DE FUENTE MATEMÁTICA / UNICODE ---
@st.cache_resource
def get_unicode_font():
    font_filename = "DejaVuSans.ttf"
    if not os.path.exists(font_filename):
        try:
            url = "https://raw.githubusercontent.com/dejavu-fonts/dejavu-fonts/master/ttf/DejaVuSans.ttf"
            urllib.request.urlretrieve(url, font_filename)
        except Exception as e:
            st.warning(f"No se pudo descargar la fuente matemática: {e}")
            return None
    return font_filename

# --- GENERACIÓN DE PDF ---
def create_pdf(student_name, codigo, grading_data, total_score, sender_email=None):
    pdf = FPDF()
    pdf.add_page()
    
    # Intentar cargar la fuente matemática para todo el documento
    font_path = get_unicode_font()
    if font_path and os.path.exists(font_path):
        pdf.add_font("DejaVu", "", font_path)
        base_font = "DejaVu"
    else:
        base_font = "Arial" # Fallback en caso de error extremo
    
    # 1. ENCABEZADO INSTITUCIONAL
    if sender_email and sender_email.strip().lower() == "carbuluj@uandina.edu.pe":
        nombre_universidad = "Universidad Andina del Cusco"
    else:
        nombre_universidad = "Universidad Nacional de San Antonio Abad del Cusco"
    
    pdf.set_font(base_font, '', 12)
    pdf.cell(0, 6, txt=nombre_universidad, ln=1, align='C')
    pdf.cell(0, 6, txt="Escuela Profesional de Ingeniería Civil", ln=1, align='C')
    pdf.cell(0, 6, txt="Docente: Mgt. César Arbulú Jurado", ln=1, align='C')
    
    pdf.ln(5)

    # 2. DATOS DEL EXAMEN
    pdf.set_font(base_font, '', 14)
    pdf.cell(0, 10, txt=f"Resultados del Control de Lectura", ln=1, align='C')
    pdf.set_font(base_font, '', 12)
    
    pdf.cell(0, 8, txt=f"Alumno: {student_name}", ln=1, align='L')
    pdf.cell(0, 8, txt=f"Código de Alumno: {codigo}", ln=1, align='L')
    pdf.cell(0, 8, txt=f"Fecha: {get_current_time_peru()}", ln=1, align='L')
    
    # 3. LÍNEA SEPARADORA
    pdf.ln(2)
    y_position = pdf.get_y()
    pdf.line(10, y_position, 200, y_position)
    pdf.ln(10)
    
    # 4. CUERPO
    for item in grading_data['detalles']:
        pdf.set_font(base_font, '', 12)
        pdf.cell(0, 10, txt=f"Pregunta {item['pregunta']} - Puntaje: {item['puntaje']}/5", ln=1)
        pdf.set_font(base_font, '', 11)
        
        # El texto ingresa PURO, con todos sus símbolos matemáticos.
        pdf.multi_cell(0, 6, txt=str(item['feedback']))
        pdf.ln(3)
        
    # 5. NOTA FINAL
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
    pdf.ln(5)
    pdf.set_font(base_font, '', 14)
    pdf.cell(0, 10, txt=f"NOTA FINAL: {total_score} / 20", ln=1, align='R')
    
    pdf.set_font(base_font, '', 11)
    
    pdf.multi_cell(0, 6, txt=f"Evaluación Global:\n{str(grading_data['comentario_final'])}")
    
    # Retornamos los bytes generados con fpdf2
    return bytes(pdf.output())

# --- INTERFAZ PRINCIPAL ---
st.set_page_config(page_title="Control de lectura", page_icon="📝")

answer_key, exam_password_sheet, sender_email_config = load_config_data()
num_questions = 4 

if not answer_key:
    if st.button("🔄 Recargar Configuración"):
        st.cache_data.clear()
        st.rerun()
    st.error("⚠️ Error cargando la configuración. Si persiste, contacte al profesor.")
    st.stop()

st.title("📝 Control de lectura")

if exam_password_sheet:
    input_code = st.text_input("🔐 Ingresa el CÓDIGO DE ACCESO:", type="password")
    
    if input_code != exam_password_sheet:
        st.info("Ingresa el código proporcionado por el profesor.")
        st.stop() 
    else:
        st.success("Acceso Autorizado ✅")

st.markdown("---")
st.write("Ingresa tus datos y sube la foto de tu examen.")

col_codigo, col_email = st.columns(2)
with col_codigo:
    codigo_alumno = st.text_input("Código de Alumno")
with col_email:
    email_alumno = st.text_input("Correo Electrónico")

name = st.text_input("Apellidos y Nombres completos")

uploaded_file = st.file_uploader("Tomar foto o subir archivo", type=['jpg', 'png', 'jpeg'])

if st.button("Enviar y Calificar"):
    if not codigo_alumno or not name or not email_alumno or not uploaded_file:
        st.warning("⚠️ Faltan datos: Asegúrate de completar Código de Alumno, Email, Nombre y Foto.")
    else:
        with st.spinner('Verificando registro...'):
            ya_existe, nota_existente = check_if_student_exists(codigo_alumno)
            
            if ya_existe:
                st.warning(f"⛔ El código {codigo_alumno} ya realizó este examen previamente.")
                st.info(f"📋 Tu nota registrada es: **{nota_existente} / 20**")
                st.error("El sistema no admite reenvíos.")
                st.stop() 

        with st.spinner('Evaluando con criterio pedagógico...'):
            result = grade_exam_with_gemini(uploaded_file, answer_key, num_questions)
            
            if result:
                try:
                    puntos = sum(item['puntaje'] for item in result['detalles'])
                    nota_final = round((puntos / (num_questions * 5)) * 20, 2)
                except:
                    nota_final = 0.0

                try:
                    wb = connect_to_sheets()
                    hoja_registro = wb.sheet1
                    hoja_registro.append_row([
                        str(codigo_alumno).strip(),
                        name, 
                        get_current_time_peru(),
                        nota_final,
                        email_alumno
                    ])
                    st.toast("✅ Nota registrada correctamente.")
                except Exception as e:
                    st.error(f"Error guardando registro: {e}")

                pdf_bytes = create_pdf(name, codigo_alumno, result, nota_final, sender_email=sender_email_config)

                st.balloons()
                st.success(f"CALIFICACIÓN COMPLETADA: **{nota_final} / 20**")

                with st.spinner('Enviando copia a tu correo...'):
                    email_enviado = send_email_with_pdf(
                        email_alumno, name, pdf_bytes,
                        sender_email_config=sender_email_config
                    )
                    if email_enviado:
                        st.success(f"📧 Se envió una copia del informe a {email_alumno}")
                    else:
                        st.warning("No se pudo enviar el correo automático, pero puedes descargar el PDF abajo.")
                
                st.download_button(
                    label="⬇️ Descargar Informe Pedagógico (PDF)",
                    data=pdf_bytes,
                    file_name=f"Informe_{codigo_alumno}.pdf",
                    mime="application/pdf"
                )