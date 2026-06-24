from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
import json
import re
import os
import hmac
import hashlib

RAZORPAY_KEY_ID = os.environ.get('RAZORPAY_KEY_ID', 'rzp_test_dummy_key_12345')
RAZORPAY_KEY_SECRET = os.environ.get('RAZORPAY_KEY_SECRET', 'dummy_secret_12345')

app = Flask(__name__)

# Allow both local development and live Vercel domain
CORS(app, resources={r"/*": {"origins": [
    "http://localhost:3000",
    "http://localhost:3001",
    "https://jtech-resume-ai.vercel.app",
    "https://jtech-resume-ai-git-main.vercel.app",
    "https://jtech-resume-ai-vino994.vercel.app"
]}})

GROQ_API_KEY = os.environ.get('GROQ_API_KEY', '')
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"


def clean_text_spacing(text):
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
    response = requests.post(GROQ_URL,
        headers={
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json"
        },
        json={
            "model": "llama-3.1-8b-instant",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 4000,
            "temperature": 0.4
        }
    )
    data = response.json()
    if 'error' in data:
        raise Exception(data['error']['message'])
    return data['choices'][0]['message']['content']


def clean_and_parse_llm_json(raw_response_text):
    if not raw_response_text:
        raise ValueError("Empty response received from LLM.")

    cleaned = raw_response_text.strip()

    # Remove markdown code fences
    if cleaned.startswith("```"):
        cleaned = re.sub(r'^```(?:json)?\s*', '', cleaned)
        cleaned = re.sub(r'\s*```$', '', cleaned)
    cleaned = cleaned.strip()

    # Isolate outermost JSON object or array
    match = re.search(r'([\[\{].*[\]\}])', cleaned, re.DOTALL)
    if match:
        cleaned = match.group(1)

    # Remove invalid control characters (keep \n and \r only)
    cleaned = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', cleaned)

    # Replace literal tabs with space
    cleaned = cleaned.replace('\t', ' ')

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as strict_err:
        # Last resort: strip all non-printable characters
        try:
            cleaned = ''.join(ch for ch in cleaned if ord(ch) >= 32 or ch in '\n\r')
            return json.loads(cleaned)
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
    return jsonify({"status": "Jtech ResumeAI Backend Running!"})


@app.route('/generate-resume', methods=['POST'])
def generate_resume():
    try:
        data = request.json
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
                        "year": f"{e.get('startYear', '').strip()} \u2013 {e.get('endYear', '').strip()}",
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
            prompt = f"""You are a professional resume writer. Write content for a FRESHER resume with NO work experience.

TARGET ROLE: {clean_text_spacing(basic_info.get('currentRole', ''))}
SKILLS PROVIDED: {basic_info.get('extraSkills', '')}
EDUCATION: {education_text}
CERTIFICATIONS: {basic_info.get('certifications', '')}
ACHIEVEMENTS: {basic_info.get('achievements', '')}
CAREER OBJECTIVE: {basic_info.get('careerObjective', '')}

YOUR REQUIRED TASKS:
1. Write a strong 3-4 line career objective for the "summary" key.
2. Generate 10-12 distinct technical skills for the target role.
3. Generate 2-3 detailed academic or personal projects relevant to the target role.

Return ONLY this exact JSON structure, no markdown, no extra text:
{{
  "summary": "Your professional career objective here.",
  "skills": ["React.js", "Node.js", "JavaScript", "TypeScript"],
  "projects": [
    {{
      "name": "Project Name",
      "tech": "Tech Stack Used",
      "description": "Clear explanation of what it does and its impact."
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
                "You are an expert ATS resume writer. Optimize this fresher internship resume.\n\n"
                f"TARGET ROLE: {clean_text_spacing(basic_info.get('currentRole', ''))}\n"
                f"SKILLS: {basic_info.get('extraSkills', '')}\n"
                f"EDUCATION: {education_text}\n\n"
                f"INTERNSHIPS:\n{companies_text}\n\n"
                "YOUR REQUIRED TASKS:\n"
                "1. Write a strong 3-4 line career objective under the 'summary' key.\n"
                "2. Generate 10-12 individual technical skills.\n"
                "3. Provide 3-4 professional bullet points for EACH internship listed.\n"
                "4. Generate 1-2 relevant academic or personal projects.\n\n"
                "Return ONLY this JSON, no markdown, no extra text:\n"
                "{\n"
                '  "summary": "Career objective here.",\n'
                '  "skills": ["React.js", "Tailwind CSS", "JavaScript"],\n'
                '  "experience": ' + exp_skeleton + ',\n'
                '  "projects": [\n'
                '    {\n'
                '      "name": "Project Name",\n'
                '      "tech": "Tech Stack",\n'
                '      "description": "Project description here."\n'
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
                 "Supported development activities and feature tracking documentation.",
                 "Participated in agile ceremonies and contributed to milestone objectives."]
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
                "You are an expert ATS resume writer. Optimize the following professional details.\n\n"
                f"TARGET ROLE: {clean_text_spacing(basic_info.get('currentRole', ''))}\n"
                f"TOTAL EXPERIENCE: {basic_info.get('totalExp', '')}\n"
                f"SKILLS: {basic_info.get('extraSkills', '')}\n\n"
                f"WORK HISTORY TO REWRITE:\n{companies_text}\n\n"
                "YOUR REQUIRED TASKS:\n"
                "1. Write a high-impact 3-4 line professional profile summary.\n"
                "2. Provide an array of 10-15 optimized technical skills.\n"
                "3. Provide 4-5 metrics-driven bullet points for EACH company listed.\n\n"
                "Return ONLY this JSON, no markdown, no extra text:\n"
                "{\n"
                '  "summary": "Professional summary here.",\n'
                '  "skills": ["React.js", "JavaScript", "TypeScript", "Node.js", "REST APIs"],\n'
                '  "experience": ' + exp_skeleton + '\n'
                "}"
            )

            text = call_groq(prompt)
            ai_result = clean_and_parse_llm_json(text)

            filtered_experience = validate_experience(
                ai_result.get('experience', []),
                companies_list,
                ["Led key frontend projects and delivered milestone updates on schedule.",
                 "Collaborated with backend teams to establish clean REST endpoints.",
                 "Implemented modern technical structures expanding interface accessibility.",
                 "Refactored complex state trees reducing production performance errors."]
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
            "You are an expert ATS resume writer. Tailor this resume to match the job description.\n\n"
            "CURRENT RESUME:\n"
            + json.dumps(resume_data, indent=2) +
            "\n\nJOB DESCRIPTION:\n"
            + jd +
            "\n\nINSTRUCTIONS:\n"
            "- Rewrite summary matching target role keywords.\n"
            "- Rewrite experience bullets to highlight metrics from the JD.\n"
            "- Update skills array.\n"
            "- Maintain original company names exactly.\n\n"
            "Return ONLY this JSON, no markdown, no extra text:\n"
            "{\n"
            '  "summary": "Tailored summary here.",\n'
            '  "experience": [\n'
            '    {\n'
            '      "company": "Exact original company name",\n'
            '      "bullets": ["Optimized bullet 1.", "Optimized bullet 2."]\n'
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
    Creates a Razorpay order for INR 39.
    Falls back to sandbox mock if real keys are not configured.
    """
    try:
        if RAZORPAY_KEY_ID == 'rzp_test_dummy_key_12345':
            return jsonify({
                "success": True,
                "sandbox": True,
                "id": "order_sandbox_mock101",
                "amount": 3900,
                "currency": "INR",
                "key": RAZORPAY_KEY_ID
            }), 200

        import razorpay
        client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))

        order_data = {
            "amount": 3900,       # INR 39.00 in paise
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
    Verifies Razorpay payment signature.
    Sandbox mode bypasses verification automatically.
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
            return jsonify({"success": False, "error": "Signature mismatch. Verification failed."}), 400

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)