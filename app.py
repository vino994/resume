from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
import json
import re
import os
import hmac
import hashlib

# Retrieve Razorpay API credentials from environment variables or default to sandbox testing keys
RAZORPAY_KEY_ID = os.environ.get('RAZORPAY_KEY_ID', 'rzp_test_dummy_key_12345')
RAZORPAY_KEY_SECRET = os.environ.get('RAZORPAY_KEY_SECRET', 'dummy_secret_12345')

app = Flask(__name__)
# Enable CORS globally across both local standard ports and production
CORS(app, resources={r"/*": {"origins": [
    "http://localhost:3000", 
    "http://localhost:3001",
    "https://your-vercel-domain-name.vercel.app" # Update this to your live Vercel URL
]}})

GROQ_API_KEY = os.environ.get('GROQ_API_KEY', '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')
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

def clean_text_spacing(text):
    """
    Utility function to automatically detect and repair word-squishing patterns 
    (e.g., 'Frontenddeveloper' -> 'Frontend Developer') to ensure optimal layout presentation.
    """
    if not text:
        return ""
    # Separate lowercase letters followed by uppercase letters (CamelCase tracking)
    cleaned = re.sub(r'([a-z])([A-Z])', r'\1 \2', text.strip())
    # Separate common squished title terms safely
    cleaned = re.sub(
        r'(?i)(frontend|backend|junior|senior|lead|fullstack|java|python|react|pvt|ltd|coimbatore|institute|of|engineering|and|science|technology|computer|science)(developer|engineer|manager|analyst|designer|architect|pvt|ltd|campus|coimbatore|institute|of|engineering|and|science|technology|computer|science)', 
        r'\1 \2', 
        cleaned
    )
    # Re-verify and clean multi-spaces
    return ' '.join(cleaned.split())

def call_groq(prompt):
    response = requests.post(GROQ_URL,
        headers={
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json"
        },
        json={
            "model": "llama-3.1-8b-instant",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 4000,
            "temperature": 0.4  # Slightly lowered for optimal structured adherence
        }
    )
    data = response.json()
    if 'error' in data:
        raise Exception(data['error']['message'])
    return data['choices'][0]['message']['content']

def clean_and_parse_llm_json(raw_response_text):
    """
    Safely sanitizes LLM markdown boundaries and cleans trailing conversational elements
    to prevent typical JSON format parsing crashes.
    """
    if not raw_response_text:
        raise ValueError("Empty response received from the LLM execution.")

    # 1. Clean markdown wrapper bounds safely
    cleaned = raw_response_text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r'^```(?:json)?\s*', '', cleaned)
        cleaned = re.sub(r'\s*```$', '', cleaned)
    cleaned = cleaned.strip()

    # 2. Isolate the outermost brackets/braces to prevent 'Extra data' trailing text errors
    match = re.search(r'([\[\{].*[\]\}])', cleaned, re.DOTALL)
    if match:
        cleaned = match.group(1)

    # 3. Escape invalid raw horizontal tab characters inside strings
    cleaned = cleaned.replace('\t', '\\t')

    # 4. Escape control characters (\x00-\x1F except \n and \r) to avoid 'Invalid control character' failures
    cleaned = re.sub(
        r'[\x00-\x1F\x7F]', 
        lambda m: f"\\u{ord(m.group(0)):04x}" if m.group(0) not in ['\n', '\r'] else m.group(0), 
        cleaned
    )

    # 5. Strict parsing execution with loose parser fallback mechanism
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as strict_err:
        try:
            # Fix unescaped double quotes inside value properties dynamically
            lazy_cleaned = re.sub(r'(?<!\\)"', '\\"', cleaned)
            lazy_cleaned = re.sub(r'\\"\s*:\s*', '": ', lazy_cleaned)
            lazy_cleaned = re.sub(r'[{,]\s*\\"', lambda m: m.group(0).replace('\\"', '"'), lazy_cleaned)
            lazy_cleaned = re.sub(r'\\"\s*([,}\]])', lambda m: m.group(0).replace('\\"', '"'), lazy_cleaned)
            return json.loads(lazy_cleaned)
        except Exception:
            raise strict_err

def build_exp_skeleton(companies_list, bullet_count=4):
    """Generates experience JSON blueprint objects without mixing LLM template variables."""
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
    """Binds LLM bullets securely back onto normalized core payload properties."""
    final_experience = []
    
    for idx, c in enumerate(companies_list):
        ai_bullets = []
        
        # Access by index alignment first
        if idx < len(ai_experience):
            ai_bullets = ai_experience[idx].get('bullets', [])
        
        # Fallback check matching by corporate name
        if not ai_bullets:
            ai_bullets = next(
                (e.get('bullets', []) for e in ai_experience 
                 if clean_text_spacing(c['company']).lower() in clean_text_spacing(e.get('company', '')).lower()), 
                []
            )
            
        # Hard fallback
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
    return jsonify({"status": "Jtech ResumeAI Backend Running!"})

@app.route('/generate-resume', methods=['POST'])
def generate_resume():
    try:
        data = request.json
        basic_info = data.get('basicInfo', {})
        profile_type = basic_info.get('profileType', 'experienced')

        # ── Clean user corporate experience payloads ──
        companies_list = [
            c for c in basic_info.get('companies', [])
            if c.get('company', '').strip() and c.get('role', '').strip()
        ]
        has_work_history = len(companies_list) > 0

        # ── Format education list parameters ──
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

        # ══════════════════════════════════════════
        # CASE 1: FRESHER — NO WORK HISTORY
        # ══════════════════════════════════════════
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

Return ONLY this exact JSON structure, no markdown, no conversational text wrapper:
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

        # ══════════════════════════════════════════
        # CASE 2: FRESHER — WITH INTERNSHIP
        # ══════════════════════════════════════════
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

        # ══════════════════════════════════════════
        # CASE 3: EXPERIENCED
        # ══════════════════════════════════════════
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
                '  "summary": "Your highly professional profile summary text goes here with standard space padding...",\n'
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
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/tailor-resume', methods=['POST'])
def tailor_resume():
    try:
        data = request.json
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
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/create-order', methods=['POST'])
def create_order():
    """
    Creates a transaction token on Razorpay for Jtech ResumeAI Premium (₹39)
    """
    try:
        if RAZORPAY_KEY_ID == 'rzp_test_dummy_key_12345':
            return jsonify({
                "success": True,
                "sandbox": True,
                "id": "order_sandbox_mock101",
                "amount": 3900,
                "currency": "INR",
                "key": "rzp_test_dummy_key_12345"
            }), 200

        import razorpay
        client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))
        
        order_data = {
            "amount": 3900,  # 39 * 100 paise
            "currency": "INR",
            "receipt": "receipt_jtech_resume_01",
            "payment_capture": 1
        }
        
        order = client.order.create(data=order_data)
        return jsonify({
            "success": True,
            "sandbox": False,
            "id": order["id"],
            "amount": order["amount"],
            "currency": order["currency"],
            "key": RAZORPAY_KEY_ID
        }), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/verify-payment', methods=['POST'])
def verify_payment():
    """
    Verifies signature confirm for Jtech transactions
    """
    try:
        data = request.json
        if data.get('sandbox') is True:
            return jsonify({"success": True, "message": "Sandbox transaction approved."}), 200

        razorpay_order_id = data.get('razorpay_order_id')
        razorpay_payment_id = data.get('razorpay_payment_id')
        razorpay_signature = data.get('razorpay_signature')

        signature_payload = f"{razorpay_order_id}|{razorpay_payment_id}"
        generated_signature = hmac.new(
            bytes(RAZORPAY_KEY_SECRET, 'utf-8'),
            bytes(signature_payload, 'utf-8'),
            hashlib.sha256
        ).hexdigest()

        if generated_signature == razorpay_signature:
            return jsonify({"success": True, "message": "Payment verified successfully!"}), 200
        else:
            return jsonify({"success": False, "error": "Signature mismatch."}), 400
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)