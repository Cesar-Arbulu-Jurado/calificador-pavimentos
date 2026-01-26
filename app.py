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
from google.api_core import exceptions

# --- CONFIGURACIÓN ---
SHEET_ID = "1LoByskK71512Gfyekk67k_OuXIbAg5BkBxq7Jcermz0"

# Configuración de Gemini (Llave oculta)
try:
    if "GEMINI_KEY" in st.secrets:
        genai.configure(api_key=st.secrets["GEMINI_KEY"])
    else:
        st.error("⚠️ Error: No encontré la llave GEMINI_KEY en los secretos.")
except Exception as e:
    st.error(f"Error configurando API Key: {e}")

# --- FUNCIÓN DE CONEXIÓN A SHEETS ---
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

# --- LÓGICA DE IA CON PROTECCIÓN ANTI-COLAPSO ---
def grade_exam_with_gemini(image_file, answer_key, num_questions):
    # 1. SELECCIÓN DE MODELO DE ALTA VELOCIDAD (De tu lista disponible)
    # Usamos Flash-Lite 001 por ser el más eficiente para concurrencia masiva
    model_name = 'gemini-2.0-flash-lite-001' 
    model = genai.GenerativeModel(model_name)
    
    image_parts = [
        {"mime_type": image_file.type, "data": image_file.getvalue()}
    ]

    prompt = f"""
    # SISTEMA DE EVALUACIÓN DE EXÁMENES MANUSCRITOS — INGENIERÍA CIVIL
    ## ROL
    Eres un evaluador académico experto en Ingeniería Civil, especializado en Pavimentos.
    
    ## CONTEXTO
    - Total de preguntas: {num_questions}
    - Escala: 0 a 5 puntos por pregunta.

    ## SOLUCIONARIO
    {answer_key}

    ## INSTRUCCIONES
    1. Transcribe la respuesta del alumno.
    2. Compara con el solucionario.
    3. Asigna puntaje (0.0 a 5.0).
    
    ## SALIDA REQUERIDA (JSON PURO)
    Devuelve estrictamente un JSON con esta estructura:
    {{
        "detalles": [
            {{
                "pregunta": 1, 
                "puntaje": 0.0, 
                "feedback": "..."
            }}
            ...
        ],
        "comentario_final": "..."
    }}
    """
    
    generation_config = {
        "temperature": 0.1,
        "max_output_tokens": 8192,
        "response_mime_type": "application/json",
    }

    # --- LÓGICA DE REINTENTOS Y JITTER (LA SOLUCIÓN TÉCNICA) ---
    max_retries = 3
    base_delay = 2 # Segundos
    
    # 1. Jitter Inicial: Espera aleatoria para no golpear la API todos a la vez
    time.sleep(random.uniform(0.1, 4.0)) 

    for attempt in range(max_retries):
        try:
            response = model.generate_content(
                [prompt, image_parts[0]], 
                generation_config=generation_config
            )
            return json.loads(response.text)

        except exceptions.ResourceExhausted:
            # Error 429: Demasiadas peticiones. Esperamos y reintentamos.
            wait_time = base_delay * (2 ** attempt) + random.uniform(0, 1)
            st.toast(f"⏳ Tráfico alto. Reintentando en {int(wait_time)}s... (Intento {attempt+1}/{max_retries})")
            time.sleep(wait_time)
            
        except Exception as e:
            # Otros errores (cortes de internet, errores 500, etc)
            st.error(f"Error técnico: {e}")
            return None
            
    st.error("❌ El sistema está saturado. Por favor intenta enviar de nuevo en 1 minuto.")
    return None

# --- GENERACIÓN DE PDF ---
def create_pdf(student_name, grading_data, total_score):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", size=12)
    
    pdf.cell(200, 10, txt=f"Resultados Examen Pavimentos", ln=1, align='C')
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
    pdf.ln(5)
    pdf.set_font("Arial", 'B', 14)
    pdf.cell(0, 10, txt=f"NOTA FINAL: {total_score} / 20", ln=1, align='R')
    
    pdf.set_font("Arial", 'I', 11)
    pdf.multi_cell(0, 10, txt=f"Comentario: {grading_data['comentario_final']}")
    
    return pdf.output(dest='S').encode('latin-1')

# --- INTERFAZ PRINCIPAL ---
st.set_page_config(page_title="Examen Pavimentos", page_icon="📝")

# 1. CARGA DE CONFIGURACIÓN
try:
    wb = connect_to_sheets()
    hoja_config = wb.worksheet("Config")
    
    data_config = hoja_config.batch_get(['A1', 'A2'])
    answer_key = data_config[0][0][0] if data_config[0] else None
    exam_password_sheet = data_config[1][0][0] if (len(data_config) > 1 and data_config[1]) else None
    
    if exam_password_sheet:
        exam_password_sheet = str(exam_password_sheet).strip()

    num_questions = 4 

    if not answer_key:
        st.error("⚠️ Falta el solucionario en la celda A1 de 'Config'.")
        st.stop()

except Exception as e:
    st.error(f"Error conectando con Google Sheets: {e}")
    st.stop()

# 2. PANTALLA DE BLOQUEO
st.title("📝 Evaluación Continua - Pavimentos")

if exam_password_sheet:
    input_code = st.text_input("🔐 Ingresa el CÓDIGO DE ACCESO:", type="password")
    
    if input_code != exam_password_sheet:
        st.info("Ingresa el código proporcionado por el profesor.")
        st.stop() 
    else:
        st.success("Acceso Autorizado ✅")

# 3. ZONA DEL ALUMNO
st.markdown("---")
st.write("Sube una foto clara de tu hoja de respuestas.")

name = st.text_input("Apellidos y Nombres completos")
uploaded_file = st.file_uploader("Tomar foto o subir archivo", type=['jpg', 'png', 'jpeg'])

if st.button("Enviar y Calificar"):
    if not name or not uploaded_file:
        st.warning("Falta tu nombre o la foto.")
    else:
        # Mensaje personalizado para pedir paciencia
        with st.spinner('Procesando... Si tarda unos segundos, es normal (estamos evitando saturar el sistema).'):
            
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
                    wb.sheet1.append_row([
                        name, 
                        datetime.now().strftime("%Y-%m-%d %H:%M"), 
                        nota_final
                    ])
                    st.toast("✅ Nota registrada correctamente.")
                except Exception as e:
                    st.error(f"Error guardando registro: {e}")

                # Resultados
                st.balloons()
                st.success(f"CALIFICACIÓN COMPLETADA: **{nota_final} / 20**")
                
                pdf_bytes = create_pdf(name, result, nota_final)
                st.download_button(
                    label="⬇️ Descargar PDF Detallado",
                    data=pdf_bytes,
                    file_name=f"Resultado_{name.replace(' ', '_')}.pdf",
                    mime="application/pdf"
                )