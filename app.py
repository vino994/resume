from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
import razorpay
import json
import re
import os
import traceback
from dotenv import load_dotenv

# Load local environment variables securely from .env file (safely ignored by Git)
load_dotenv()

app = Flask(__name__)

# Configured CORS to allow both localhost ports and the production Vercel frontend domain
CORS(app, resources={r"/*": {
    "origins": [
        "http://localhost:3000",
        "http://localhost:3001",
        "https://jtech-resume-ai.vercel.app"
    ],
    "methods": ["GET", "POST", "OPTIONS"],
    "allow_headers": ["Content-Type", "Authorization"]
}})

# Securely fetch the API key from environment configuration
GROQ_API_KEY = os.environ.get('GROQ_API_KEY')
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

RAZORPAY_KEY_ID = os.environ.get('RAZORPAY_KEY_ID', '')
RAZORPAY_KEY_SECRET = os.environ.get('RAZORPAY_KEY_SECRET', '')

try:
    if RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET:
        rz_client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))
    else:
        rz_client = None
except Exception:
    rz_client = None

def clean_text_spacing(text):
    """
    Utility function to automatically detect and repair word-squishing patterns 
    (e.g., 'Frontenddeveloper' -> 'Frontend Developer') to ensure optimal layout presentation.
    """
    if not text:
        return ""
    cleaned = re.sub(r'([a-z])([A-Z])', r'\1 \2', text.strip())
    cleaned = re.sub(
        r'(?i)(frontend|backend|junior|senior|lead|fullstack|java|python|react|pvt|ltd|coimbatore|institute|of|engineering|and|science|technology|computer|science)(developer|engineer|manager|analyst|designer|architect|pvt|ltd|campus|coimbatore|institute|of|engineering|and|science|technology|computer|science)', 
        r'\1 \2', 
        cleaned
    )
    return ' '.join(cleaned.split())

def call_groq(prompt):
    print("[DEBUG] Sending prompt request to Groq API...")
    response = requests.post(GROQ_URL,
        headers={
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json"
        },
        json={
            "model": "llama-3.1-8b-instant",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 4000,
            "temperature": 0.1,  # Lowered temperature to minimize creative syntactic variations
            "response_format": {"type": "json_object"}  # Native JSON Mode activation!
        }
    )
    data = response.json()
    if 'error' in data:
        print(f"[DEBUG] Groq returned API error: {data['error']}")
        raise Exception(data['error']['message'])
    return data['choices'][0]['message']['content']

def clean_and_parse_llm_json(raw_response_text):
    """
    Safely sanitizes LLM markdown boundaries, escaping internal control characters and nested
    quotes using safe lookahead/lookbehind whitespace-insensitive heuristics.
    """
    if not raw_response_text:
        raise ValueError("Empty response received from the LLM execution.")

    cleaned = raw_response_text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r'^```(?:json)?\s*', '', cleaned)
        cleaned = re.sub(r'\s*```$', '', cleaned)
    cleaned = cleaned.strip()

    match = re.search(r'([\[\{].*[\]\}])', cleaned, re.DOTALL)
    if match:
        cleaned = match.group(1)

    cleaned = cleaned.replace('\t', '\\t')

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as strict_err:
        print("[HEALER] Strict parsing failed. Running state-aware JSON structural healing...")
        try:
            healed = []
            in_string = False
            escape_next = False
            for i, char in enumerate(cleaned):
                if escape_next:
                    healed.append(char)
                    escape_next = False
                    continue
                if char == '\\':
                    healed.append(char)
                    escape_next = True
                    continue
                if char == '"':
                    # Skip spaces lookbehind
                    left_idx = i - 1
                    while left_idx >= 0 and cleaned[left_idx].isspace():
                        left_idx -= 1
                    left_char = cleaned[left_idx] if left_idx >= 0 else ''
                    
                    # Skip spaces lookahead
                    right_idx = i + 1
                    while right_idx < len(cleaned) and cleaned[right_idx].isspace():
                        right_idx += 1
                    right_char = cleaned[right_idx] if right_idx < len(cleaned) else ''
                    
                    # Determine structural boundaries cleanly
                    is_structural = False
                    if left_char in ('{', '[', ',', ':'):
                        is_structural = True
                    elif right_char in (':', ',', '}', ']'):
                        is_structural = True
                    elif left_idx < 0 or right_idx >= len(cleaned):
                        is_structural = True
                    
                    if is_structural:
                        in_string = not in_string
                        healed.append(char)
                    else:
                        if in_string:
                            healed.append('\\"')  # Escape raw internal quote safely!
                        else:
                            in_string = True
                            healed.append(char)
                else:
                    if char == '\n' and in_string:
                        healed.append('\\n')
                    elif char == '\r' and in_string:
                        healed.append('\\r')
                    else:
                        healed.append(char)
            
            healed_text = "".join(healed)
            
            # Auto-strip standard LLM trailing commas that violate strict JSON parsing
            healed_text = re.sub(r',\s*\}', '}', healed_text)
            healed_text = re.sub(r',\s*\]', ']', healed_text)
            
            return json.loads(healed_text)
        except Exception:
            raise strict_err

def build_exp_skeleton(companies_list, bullet_count=4):
    skeleton = []
    for c in companies_list:
        skeleton.append({
            "company": clean_text_spacing(c['company']),
            "role": clean_text_spacing(c['role']),
            "duration": clean_text_spacing(c.get('duration', '')),
            "bullets": [f"write strong bullet {i+1} here" for i in range(bullet_count)]
        })
    return json.dumps(skeleton, indent=2)

def validate_experience(ai_experience, companies_list, default_bullets):
    final_experience = []
    for idx, c in enumerate(companies_list):
        ai_bullets = []
        if idx < len(ai_experience):
            ai_bullets = ai_experience[idx].get('bullets', [])
        if not ai_bullets:
            ai_bullets = next(
                (e.get('bullets', []) for e in ai_experience 
                 if clean_text_spacing(c['company']).lower() in clean_text_spacing(e.get('company', '')).lower()), 
                []
            )
        if not ai_bullets:
            ai_bullets = default_bullets

        final_experience.append({
            "company": clean_text_spacing(c['company']),
            "role": clean_text_spacing(c['role']),
            "duration": clean_text_spacing(c.get('duration', '')),
            "bullets": [clean_text_spacing(b) for b in ai_bullets if b.strip()]
        })
    return final_experience

@app.route('/', methods=['GET'])
def home():
    return jsonify({"status": "ResumeAI Backend Running!"})

@app.route('/generate-resume', methods=['POST'])
def generate_resume():
    try:
        data = request.json or {}
        basic_info = data.get('basicInfo', {})
        profile_type = basic_info.get('profileType', 'experienced')

        companies_list = [
            c for c in basic_info.get('companies', [])
            if c.get('company', '').strip() and c.get('role', '').strip()
        ]
        has_work_history = len(companies_list) > 0

        education_list = basic_info.get('education', [])
        education_text = ''
        parsed_education = []
        if isinstance(education_list, list):
            for e in education_list:
                if e.get('institution', '').strip():
                    inst = clean_text_spacing(e.get('institution', ''))
                    deg = clean_text_spacing(e.get('degree', ''))
                    br = clean_text_spacing(e.get('branch', ''))
                    education_text += (
                        f"- {deg} in {br} from {inst} "
                        f"({e.get('startYear','')} to {e.get('endYear','')}) "
                        f"Score: {e.get('percentage','')}\n"
                    )
                    parsed_education.append({
                        "institution": inst,
                        "degree": deg,
                        "branch": br,
                        "year": f"{e.get('startYear', '').strip()} – {e.get('endYear', '').strip()}",
                        "percentage": e.get('percentage', '').strip()
                    })

        parsed_certifications = [clean_text_spacing(c) for c in basic_info.get('certifications', '').split(',') if c.strip()]
        parsed_languages = [clean_text_spacing(l) for l in basic_info.get('languages', '').split(',') if l.strip()]
        parsed_achievements = [clean_text_spacing(a) for a in basic_info.get('achievements', '').replace(',', '.').split('.') if a.strip()]

        companies_text = '\n'.join([
            f"Index {i}: Company: {clean_text_spacing(c['company'])} | Role: {clean_text_spacing(c['role'])} | Duration: {c.get('duration', '')}"
            for i, c in enumerate(companies_list)
        ])

        if profile_type == 'fresher' and not has_work_history:
            prompt = f"""You are a professional resume writer. Write content for a FRESHER resume with NO work experience. Ensure proper standard spacing between descriptive terms, adjectives, and title tokens.

TARGET ROLE: {clean_text_spacing(basic_info.get('currentRole', ''))}
SKILLS PROVIDED: {basic_info.get('extraSkills', '')}
EDUCATION: {education_text}
CERTIFICATIONS: {basic_info.get('certifications', '')}
ACHIEVEMENTS: {basic_info.get('achievements', '')}
CAREER OBJECTIVE: {basic_info.get('careerObjective', '')}

YOUR REQUIRED TASKS:
1. Write a strong, readable 3-4 line career objective for the "summary" key.
2. Generate 10-12 distinct, properly spaced technical skills for the target role.
3. Generate 2-3 detailed academic or personal projects relevant to the target role.

Return ONLY this exact JSON structure:
{{
  "summary": "Your professional career objective writeup here with clear spacing.",
  "skills": ["React.js", "Node.js", "JavaScript", "TypeScript"],
  "projects": [
    {{
      "name": "Project Name",
      "tech": "Tech Stack Used",
      "description": "Clear explanation of what it does and its structural business impact."
    }}
  ]
}}"""

            text = call_groq(prompt)
            ai_result = clean_and_parse_llm_json(text)

            result = {
                "name": clean_text_spacing(basic_info.get('name', '')),
                "email": basic_info.get('email', '').strip(),
                "phone": basic_info.get('phone', '').strip(),
                "linkedin": basic_info.get('linkedin', '').strip(),
                "github": basic_info.get('github', '').strip(),
                "portfolio": basic_info.get('portfolio', '').strip(),
                "currentRole": clean_text_spacing(basic_info.get('currentRole', '')),
                "profileType": "fresher",
                "summary": clean_text_spacing(ai_result.get('summary', '')),
                "skills": [clean_text_spacing(s) for s in ai_result.get('skills', [])],
                "experience": [],
                "education": parsed_education,
                "certifications": parsed_certifications,
                "languages": parsed_languages,
                "achievements": parsed_achievements,
                "projects": ai_result.get('projects', [])
            }

        elif profile_type == 'fresher' and has_work_history:
            exp_skeleton = build_exp_skeleton(companies_list, bullet_count=4)

            prompt = (
                "You are an expert ATS resume writer. Optimize this fresher internship resume. Ensure natural language spaces are placed cleanly between technical roles and descriptions.\n\n"
                f"TARGET ROLE: {clean_text_spacing(basic_info.get('currentRole', ''))}\n"
                f"SKILLS: {basic_info.get('extraSkills', '')}\n"
                f"EDUCATION: {education_text}\n\n"
                f"INTERNSHIPS:\n{companies_text}\n\n"
                "YOUR REQUIRED TASKS:\n"
                "1. Write a strong, readable 3-4 line career objective under the 'summary' key.\n"
                "2. Generate 10-12 discrete, individual technical skills arrays.\n"
                "3. Provide 3-4 professional, metrics-focused bullet points for EACH internship entry listed below.\n"
                "4. Generate 1-2 relevant academic or side engineering projects.\n\n"
                "You MUST respond ONLY with this clean, standardized JSON template format:\n"
                "{\n"
                '  "summary": "Clear, well-spaced career objective written here...",\n'
                '  "skills": ["React.js", "Tailwind CSS", "JavaScript"],\n'
                '  "experience": ' + exp_skeleton + ',\n'
                '  "projects": [\n'
                '    {\n'
                '      "name": "E-Commerce App",\n'
                '      "tech": "MERN Stack",\n'
                '      "description": "Architected complex user dashboards and database integrations seamlessly."\n'
                '    }\n'
                '  ]\n'
                "}"
            )

            text = call_groq(prompt)
            ai_result = clean_and_parse_llm_json(text)

            filtered_experience = validate_experience(
                ai_result.get('experience', []),
                companies_list,
                ["Assisted in project tasks and team collaboration.",
                 "Supported development activities and clear feature tracking documentation.",
                 "Participated in agile ceremonies and directly contributed to milestone objectives."]
            )

            result = {
                "name": clean_text_spacing(basic_info.get('name', '')),
                "email": basic_info.get('email', '').strip(),
                "phone": basic_info.get('phone', '').strip(),
                "linkedin": basic_info.get('linkedin', '').strip(),
                "github": basic_info.get('github', '').strip(),
                "portfolio": basic_info.get('portfolio', '').strip(),
                "currentRole": clean_text_spacing(basic_info.get('currentRole', '')),
                "profileType": "fresher",
                "summary": clean_text_spacing(ai_result.get('summary', '')),
                "skills": [clean_text_spacing(s) for s in ai_result.get('skills', [])],
                "experience": filtered_experience,
                "education": parsed_education,
                "certifications": parsed_certifications,
                "languages": parsed_languages,
                "achievements": parsed_achievements,
                "projects": ai_result.get('projects', [])
            }

        else:
            exp_skeleton = build_exp_skeleton(companies_list, bullet_count=5)
            
            prompt = (
                "You are an expert ATS resume writer. Optimize the following professional details. Ensure clear, readable spaces are left between distinct vocabulary words.\n\n"
                f"TARGET ROLE: {clean_text_spacing(basic_info.get('currentRole', ''))}\n"
                f"TOTAL EXPERIENCE: {basic_info.get('totalExp', '')}\n"
                f"SKILLS: {basic_info.get('extraSkills', '')}\n\n"
                f"WORK HISTORY TO REWRITE:\n{companies_text}\n\n"
                "YOUR REQUIRED TASKS:\n"
                "1. Write a high-impact, beautifully spaced 3-4 line professional profile summary.\n"
                "2. Provide an array of 10-15 highly optimized individual technical keywords/skills.\n"
                "3. Provide an array of objects containing 4-5 high-impact, metrics-driven bullet points for EACH company index sequence.\n\n"
                "You MUST respond ONLY with this clean, standardized JSON format block:\n"
                "{\n"
                '  "summary": "Your highly professional profile summary text goes here...",\n'
                '  "skills": ["React.js", "JavaScript", "TypeScript", "Node.js", "REST APIs", "Redux Toolkit"],\n'
                '  "experience": ' + exp_skeleton + '\n'
                "}"
            )

            text = call_groq(prompt)
            ai_result = clean_and_parse_llm_json(text)

            filtered_experience = validate_experience(
                ai_result.get('experience', []),
                companies_list,
                ["Led key frontend application projects and delivered target milestone updates on schedule.",
                 "Collaborated seamlessly with backend teams to establish clean endpoints and system components.",
                 "Implemented modern technical structures expanding client interface accessibility paths.",
                 "Refactored complex state trees reducing production performance paint block errors."]
            )

            result = {
                "name": clean_text_spacing(basic_info.get('name', '')),
                "email": basic_info.get('email', '').strip(),
                "phone": basic_info.get('phone', '').strip(),
                "linkedin": basic_info.get('linkedin', '').strip(),
                "github": basic_info.get('github', '').strip(),
                "portfolio": basic_info.get('portfolio', '').strip(),
                "currentRole": clean_text_spacing(basic_info.get('currentRole', '')),
                "profileType": "experienced",
                "summary": clean_text_spacing(ai_result.get('summary', '')),
                "skills": [clean_text_spacing(s) for s in ai_result.get('skills', [])] if ai_result.get('skills') else [clean_text_spacing(s) for s in basic_info.get('extraSkills', '').split(',') if s.strip()],
                "experience": filtered_experience,
                "education": parsed_education,
                "certifications": parsed_certifications,
                "languages": parsed_languages,
                "achievements": parsed_achievements,
                "projects": []
            }

        return jsonify({'success': True, 'data': result})

    except Exception as e:
        print("[CRITICAL ERROR] Crash inside /generate-resume route:")
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/tailor-resume', methods=['POST'])
def tailor_resume():
    try:
        data = request.json or {}
        resume_data = data.get('resumeData', {})
        jd = data.get('jobDescription', '')

        prompt = (
            "You are an expert ATS resume writer. Tailor this resume structure to match the job description parameters closely while preserving text spaces.\n\n"
            "CURRENT RESUME:\n"
            + json.dumps(resume_data, indent=2) +
            "\n\nJOB DESCRIPTION:\n"
            + jd +
            "\n\nINSTRUCTIONS:\n"
            "- Rewrite summary key matching target role keywords.\n"
            "- Rewrite experience bullets to highlight critical metrics from the JD.\n"
            "- Update skills array data strings.\n"
            "- Maintain original company tracking tags exactly.\n\n"
            "Return ONLY valid JSON format structures:\n"
            "{\n"
            '  "summary": "tailored summary with spacing",\n'
            '  "experience": [\n'
            '    {\n'
            '      "company": "exact original tag",\n'
            '      "bullets": ["optimized bullet point 1", "optimized bullet point 2"]\n'
            '    }\n'
            '  ],\n'
            '  "skills": ["keyword1", "keyword2"]\n'
            "}"
        )

        text = call_groq(prompt)
        result = clean_and_parse_llm_json(text)
        return jsonify({'success': True, 'data': result})

    except Exception as e:
        print("[CRITICAL ERROR] Crash inside /tailor-resume route:")
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/create-order', methods=['POST'])
def create_order():
    """Generates order templates for live or mock payment pipelines securely."""
    try:
        if not RAZORPAY_KEY_ID or not RAZORPAY_KEY_SECRET or not rz_client:
            return jsonify({
                "success": True,
                "sandbox": True,
                "key": "rzp_test_mock_jtech_key_994",
                "amount": 3900,
                "currency": "INR",
                "id": "order_mock_sandbox_39"
            })
        
        order_data = {
            "amount": 3900,
            "currency": "INR",
            "payment_capture": 1
        }
        order = rz_client.order.create(data=order_data)
        return jsonify({
            "success": True,
            "sandbox": False,
            "key": RAZORPAY_KEY_ID,
            "amount": order['amount'],
            "currency": order['currency'],
            "id": order['id']
        })
    except Exception as e:
        print("[CRITICAL ERROR] Crash inside /create-order route:")
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/verify-payment', methods=['POST'])
def verify_payment():
    """Authenticates signature validations on completed order packages."""
    try:
        data = request.json or {}
        is_sandbox = data.get('sandbox', False)
        
        if is_sandbox:
            return jsonify({"success": True, "message": "Sandbox Jtech Payment Approved!"})
        
        params_dict = {
            'razorpay_order_id': data.get('razorpay_order_id'),
            'razorpay_payment_id': data.get('razorpay_payment_id'),
            'razorpay_signature': data.get('razorpay_signature')
        }
        
        rz_client.utility.verify_payment_signature(params_dict)
        return jsonify({"success": True, "message": "Payment verified successfully!"})
    except Exception as e:
        print("[CRITICAL ERROR] Crash inside /verify-payment route:")
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 400

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
