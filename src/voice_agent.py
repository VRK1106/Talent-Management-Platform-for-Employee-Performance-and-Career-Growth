import json
import re
import sqlite3
import traceback
from src.users import _DB_PATH
from src.exams import add_exam, add_announcement, get_all_exams, get_all_announcements, add_exam_and_get_id, assign_exam_to_all_students
from src.student_performance import get_student_performance_context, get_aggregate_performance_context
from src.llm import generate_chat_answer, clean_json_response
from src.vectorstore import get_collection
from src.sprints import save_study_plan

VOICE_AGENT_SYSTEM_PROMPT = """You are Sphere Voice AI, a totally voice-based intelligent assistant for Talent Sphere Elevate.
You talk to the user naturally via voice.

Capabilities:
1. Access web app data (documents in vectorstore, trainee list, student performance scores, current exams, recent announcements).
2. Create a professional Corporate Training Announcement.
3. Create a new Exam based on one or multiple combined PDF study reference files in the database.
4. Create a new 6-day Sprint Study Plan with daily tasks, reference files, and Day 5 Gateway Exam.

CONVERSATION GUIDELINES:
- Keep your verbal responses concise, friendly, and clear (typically 20-50 words per turn) as they will be spoken aloud to the user.
- If you need information from the database or need to perform a write action, you MUST output a command tag in your response. The system will run the command and provide the results in the next turn.
- To execute an action, output a single command block formatted exactly as:
  [COMMAND: {"action": "action_name", "param1": "val1", ...}]
- Do NOT output commands unless you have gathered all necessary information from the user first.

SYSTEM RESULT HANDLING:
- When a command runs and returns [SYSTEM RESULT: ...], you MUST read the result carefully and base your response strictly on that data.
- If the [SYSTEM RESULT: ...] contains a list of items (exams, documents, trainees, announcements), affirm to the user that the items were retrieved and listed on screen for them to select, and name the items clearly. Never say items do not exist if they are listed in [SYSTEM RESULT: ...].

Available commands:
1. `{"action": "list_docs"}` - Returns list of reference PDFs available.
2. `{"action": "list_trainees"}` - Returns list of trainee employees.
3. `{"action": "list_exams"}` - Returns list of existing exams in system.
4. `{"action": "list_announcements"}` - Returns list of announcements.
5. `{"action": "get_performance", "identifier": "<employee_id or trainee_name>"}` - Returns trainee exam logs and analytics.
6. `{"action": "get_all_performance"}` - Returns aggregate platform performance.
7. `{"action": "create_announcement", "title": "<title>", "content": "<content>"}` - Generates and saves an announcement.
8. `{"action": "create_exam", "title": "<title>", "doc": "<document_filename or comma-separated filenames>", "question_count": <int>, "marks_per_question": <int>, "difficulty": "<easy|medium|hard>"}` - Generates and saves a new exam based on one or multiple combined documents.
9. `{"action": "create_study_plan", "title": "<title>", "domain": "<domain>", "docs": "<document_filename or comma-separated filenames>", "week_number": <int>}` - Generates and saves a new 6-day Sprint Study Plan with daily tasks, reference files, and Day 5 Gateway Exam.

Remember: Output at most ONE [COMMAND: ...] tag per turn. Keep the rest of your text conversational and spoken-friendly. Do not mention the command syntax to the user.
"""

def execute_agent_action(command_dict: dict) -> str:
    """Executes a parsed command dictionary and returns a system description of the result."""
    action = command_dict.get("action", "")
    try:
        if action == "list_docs":
            coll = get_collection()
            res = coll.get(include=["metadatas"])
            metadatas = res.get("metadatas") or []
            docs = sorted(list(set(m["source"] for m in metadatas if m and "source" in m)))
            command_dict["items"] = docs
            command_dict["select_mode"] = "multi"
            command_dict["item_type"] = "document"
            if not docs:
                return "No reference PDF documents are ingested in the system yet."
            return f"Ingested reference documents: {', '.join(docs)}"

        elif action == "list_trainees":
            conn = sqlite3.connect(str(_DB_PATH))
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT employee_id, full_name, domain FROM users WHERE role = 'trainee'")
            rows = cursor.fetchall()
            trainees = [f"{r['full_name']} ({r['employee_id']})" for r in rows]
            command_dict["items"] = trainees
            command_dict["select_mode"] = "multi"
            command_dict["item_type"] = "trainee"
            conn.close()
            if not trainees:
                return "No trainee users exist in the system database."
            return f"Trainees: {'; '.join(trainees)}"

        elif action == "list_exams":
            exams = get_all_exams()
            exam_titles = [e.get("title") for e in exams if e.get("title")]
            command_dict["items"] = exam_titles
            command_dict["select_mode"] = "multi"
            command_dict["item_type"] = "exam"
            if not exam_titles:
                return "No exams found in the database."
            return f"Exams available: {', '.join(exam_titles)}"

        elif action == "list_announcements":
            announcements = get_all_announcements()
            ann_titles = [a.get("title") for a in announcements if a.get("title")]
            command_dict["items"] = ann_titles
            command_dict["select_mode"] = "multi"
            command_dict["item_type"] = "announcement"
            if not ann_titles:
                return "No announcements found in the database."
            return f"Announcements: {', '.join(ann_titles)}"

        elif action == "get_performance":
            ident = command_dict.get("identifier", "").strip()
            if not ident:
                return "Error: Please specify a trainee identifier."
            context = get_student_performance_context(ident, "admin", "admin")
            return f"Trainee Performance details for '{ident}':\n{context}"

        elif action == "get_all_performance":
            context = get_aggregate_performance_context("admin")
            conn = sqlite3.connect(str(_DB_PATH))
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT employee_id, full_name FROM users WHERE role = 'trainee'")
            rows = cursor.fetchall()
            trainees = [f"{r['full_name']} ({r['employee_id']})" for r in rows]
            conn.close()
            if trainees:
                command_dict["items"] = trainees
                command_dict["select_mode"] = "multi"
                command_dict["item_type"] = "trainee for report"
            return f"Aggregate platform performance summary:\n{context}"

        elif action == "create_announcement":
            title = command_dict.get("title", "").strip()
            content = command_dict.get("content", "").strip()
            if not title or not content:
                return "Error: Both title and content are required to create an announcement."
            
            # Use exams.py function to save
            success = add_announcement(title, content, send_email=True)
            if success:
                return f"Success: Announcement '{title}' created and saved. Email notifications sent."
            else:
                return "Error: Failed to save the announcement to the database."

        elif action in ["create_exam", "combine_docs", "combine_documents", "create_combined_exam"]:
            title = command_dict.get("title", "").strip()
            doc_param = command_dict.get("doc") or command_dict.get("docs") or command_dict.get("documents") or ""
            q_count = int(command_dict.get("question_count", 5))
            marks = int(command_dict.get("marks_per_question", 10))
            difficulty = command_dict.get("difficulty", "medium").lower()

            # Parse doc parameter into list of document filenames
            requested_names = []
            if isinstance(doc_param, list):
                requested_names = [str(d).strip() for d in doc_param if str(d).strip()]
            elif isinstance(doc_param, str):
                requested_names = [d.strip() for d in doc_param.split(",") if d.strip()]

            if not requested_names:
                return "Error: At least one reference document filename is required to create an exam."

            # Retrieve available documents from vector store for fuzzy matching
            coll = get_collection()
            all_res = coll.get(include=["metadatas"])
            all_metas = all_res.get("metadatas") or []
            all_docs = sorted(list(set(m["source"] for m in all_metas if m and "source" in m)))

            resolved_docs = []
            for req in requested_names:
                req_lower = req.lower()
                matched = next((ad for ad in all_docs if ad.lower() == req_lower), None)
                if not matched:
                    matched = next((ad for ad in all_docs if req_lower in ad.lower() or ad.lower() in req_lower), None)
                if matched:
                    if matched not in resolved_docs:
                        resolved_docs.append(matched)
                else:
                    resolved_docs.append(req)

            if not title:
                title = f"Combined Exam ({', '.join(resolved_docs)})"

            # Gather chunks across all resolved documents
            chunks = []
            for d in resolved_docs:
                res = coll.get(where={"source": d}, include=["documents"])
                d_chunks = res.get("documents") or []
                if d_chunks:
                    chunks.extend(d_chunks[:3])

            if not chunks:
                return f"Error: None of the reference documents ({', '.join(resolved_docs)}) have text content in the vector DB."
            
            context_text = "\n\n--- DOCUMENT BREAK ---\n\n".join(chunks[:6])
            doc_summary_name = " & ".join(resolved_docs)
            
            # Construct question generator prompt
            generator_prompt = (
                f"Generate exactly {q_count} multiple-choice questions (MCQs) for an exam titled '{title}' based on the combined document excerpts below.\n\n"
                f"Constraints:\n"
                f"- Section Type: MCQ\n"
                f"- Marks per question: {marks}\n"
                f"- Difficulty target: {difficulty}\n"
                f"Format of each question object in JSON:\n"
                f"[{{\n"
                f"  \"question\": \"Question text here\",\n"
                f"  \"type\": \"mcq\",\n"
                f"  \"marks\": {marks},\n"
                f"  \"options\": [\"Option A\", \"Option B\", \"Option C\", \"Option D\"],\n"
                f"  \"correct_answer\": \"Correct option text exactly\"\n"
                f"}}]\n\n"
                f"You MUST return ONLY a valid JSON array of question objects (do not wrap in markdown or prefix text).\n\n"
                f"--- COMBINED DOCUMENT EXCERPTS ({doc_summary_name}) ---\n{context_text}"
            )
            
            raw_response = generate_chat_answer(
                prompt=generator_prompt,
                model_name="llama-3.3-70b-versatile",
                system_instruction="You are a professional educational assessor. You output ONLY valid JSON arrays without markdown block wrapping or prefix text."
            )
            
            cleaned = raw_response.strip()
            if cleaned.startswith("```json"):
                cleaned = cleaned[7:]
            if cleaned.startswith("```"):
                cleaned = cleaned[3:]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            cleaned = cleaned.strip()

            try:
                questions = json.loads(cleaned)
                if not isinstance(questions, list):
                    raise ValueError("Response is not a JSON list")
                
                for q in questions:
                    q["section"] = "Combined Section"
                
                # Save Exam
                description = f"Generated automatically by Voice Assistant combining reference documents: '{doc_summary_name}'."
                total_marks = q_count * marks
                settings = {
                    "scheduling": {
                        "start_time": "",
                        "end_time": "",
                        "duration": 60
                    },
                    "security": {
                        "tab_lock": True,
                        "face_proctoring": True,
                        "browser_integrity": True
                    }
                }
                
                success = add_exam(title, description, total_marks, questions, settings=settings)
                if success:
                    return f"Success: Combined Exam '{title}' with {len(questions)} questions generated and saved based on '{doc_summary_name}'."
                else:
                    return "Error: Failed to save the generated exam to the database."
            except Exception as e:
                print(f"Exam Generation JSON parsing error: {e}. Raw: {raw_response}")
                return f"Error: Failed to parse generated questions. Error details: {str(e)}"

        elif action in ["create_study_plan", "generate_study_plan", "create_sprint_plan"]:
            title = command_dict.get("title", "").strip()
            domain = command_dict.get("domain", "general").strip().lower()
            week_num = int(command_dict.get("week_number", 1))
            doc_param = command_dict.get("doc") or command_dict.get("docs") or command_dict.get("documents") or ""

            requested_names = []
            if isinstance(doc_param, list):
                requested_names = [str(d).strip() for d in doc_param if str(d).strip()]
            elif isinstance(doc_param, str):
                requested_names = [d.strip() for d in doc_param.split(",") if d.strip()]

            coll = get_collection()
            all_res = coll.get(include=["metadatas"])
            all_metas = all_res.get("metadatas") or []
            all_docs = sorted(list(set(m["source"] for m in all_metas if m and "source" in m)))

            resolved_docs = []
            for req in requested_names:
                req_lower = req.lower()
                matched = next((ad for ad in all_docs if ad.lower() == req_lower), None)
                if not matched:
                    matched = next((ad for ad in all_docs if req_lower in ad.lower() or ad.lower() in req_lower), None)
                if matched:
                    if matched not in resolved_docs:
                        resolved_docs.append(matched)
                else:
                    resolved_docs.append(req)

            if not resolved_docs and all_docs:
                resolved_docs = all_docs[:4]

            docs_contents = []
            for dn in resolved_docs:
                try:
                    res = coll.get(where={"source": dn}, include=["documents"])
                    d_list = res.get("documents") or []
                    c_text = "\n\n".join(d_list[:20]) if d_list else f"Reference Content for {dn}"
                    docs_contents.append({"source": dn, "text": c_text})
                except Exception:
                    docs_contents.append({"source": dn, "text": f"Reference Content for {dn}"})

            num_files = len(docs_contents)
            tasks = {}
            if num_files >= 4:
                tasks = {
                    "day1": [f"[{docs_contents[0]['source']}] {docs_contents[0]['text'][:350]}"],
                    "day2": [f"[{docs_contents[1]['source']}] {docs_contents[1]['text'][:350]}"],
                    "day3": [f"[{docs_contents[2]['source']}] {docs_contents[2]['text'][:350]}"],
                    "day4": [f"[{docs_contents[3]['source']}] {docs_contents[3]['text'][:350]}"]
                }
            elif num_files > 0:
                tasks = {}
                for d_idx in range(1, 5):
                    src_doc = docs_contents[(d_idx-1) % num_files]
                    tasks[f"day{d_idx}"] = [f"[{src_doc['source']}] Module {d_idx} Key Concepts and Practice Exercises"]
            else:
                tasks = {
                    "day1": [f"[{domain.capitalize()}] Module 1 Core Principles and Architecture"],
                    "day2": [f"[{domain.capitalize()}] Module 2 Advanced Implementation Patterns"],
                    "day3": [f"[{domain.capitalize()}] Module 3 Testing, Security, and Optimization"],
                    "day4": [f"[{domain.capitalize()}] Module 4 Capstone Integration Checkpoint"]
                }

            plan_title = title or f"{domain.capitalize()} Week {week_num} AI Sprint Plan"
            target_q_count = max(4, num_files * 3 if num_files > 0 else 6)
            
            exam_prompt = (
                f"Generate an exam based on the following training materials for {domain.capitalize()} Week {week_num}.\n"
                f"Generate EXACTLY {target_q_count} multiple-choice questions.\n"
                f"CRITICAL: For every question, provide 4 distinct technical options. No placeholders.\n"
                f"Output ONLY a valid JSON list of objects.\n"
            )
            for idx, d in enumerate(docs_contents):
                exam_prompt += f"--- File {idx+1} ({d['source']}) ---\n{d['text'][:1000]}\n\n"

            questions_list = []
            try:
                resp = generate_chat_answer(prompt=exam_prompt, model_name="llama-3.3-70b-versatile", system_instruction="Output ONLY valid JSON array.")
                cleaned = clean_json_response(resp)
                questions_list = json.loads(cleaned)
            except Exception:
                questions_list = []

            cleaned_questions = []
            for idx, q in enumerate(questions_list):
                if not isinstance(q, dict):
                    continue
                opts = q.get('options', [])
                has_placeholder = any(any(ph in str(opt).lower() for ph in ['option a', 'option b', 'incorrect option', 'correct concept']) for opt in opts)
                if len(opts) < 4 or has_placeholder:
                    opts = [
                        f"Primary Standard in {domain.capitalize()} (Module {(idx%4)+1})",
                        f"Secondary Methodology (Section {(idx%4)+2})",
                        f"Alternative Implementation Choice",
                        f"Legacy Non-Compliant Standard"
                    ]
                    q['options'] = opts
                    q['correct_answer'] = opts[0]
                cleaned_questions.append(q)

            if not cleaned_questions:
                for i in range(target_q_count):
                    opts = [
                        f"Core Principle in {domain.capitalize()} (Unit {(i%4)+1})",
                        f"Secondary Process Variant",
                        f"Non-Compliant Legacy Specification",
                        f"Unrelated System Property"
                    ]
                    cleaned_questions.append({
                        "question": f"What is a key architectural requirement in {domain.capitalize()} Module {(i%4)+1}?",
                        "type": "mcq",
                        "marks": 10,
                        "options": opts,
                        "correct_answer": opts[0]
                    })

            exam_title = f"Day 5 Gateway Exam: {plan_title}"
            total_marks = len(cleaned_questions) * 10
            exam_id = add_exam_and_get_id(exam_title, f"Gateway Exam for {plan_title}.", total_marks, cleaned_questions)
            if exam_id:
                try:
                    assign_exam_to_all_students(exam_id)
                except Exception:
                    pass

            ref_files = [d['source'] for d in docs_contents]
            save_study_plan(
                domain=domain,
                week_number=week_num,
                title=plan_title,
                tasks_json=json.dumps(tasks),
                day5_exam_id=str(exam_id) if exam_id else "",
                day6_interview_prompt=f"Defend the core architecture and key principles from {plan_title}.",
                reference_files_json=json.dumps(ref_files)
            )

            return f"Success: Created 6-Day Study Plan '{plan_title}' for domain '{domain}' with {len(cleaned_questions)} Day 5 Gateway Exam questions and linked reference files ({', '.join(ref_files) if ref_files else 'Standard Modules'})."

        else:
            return f"Error: Unknown action '{action}'."

    except Exception as e:
        traceback.print_exc()
        return f"Error executing action '{action}': {str(e)}"

def run_voice_agent(query: str, history: list, model_name: str = "llama-3.3-70b-versatile") -> tuple[str, dict | None, list]:
    """Runs the voice agent loop. Accepts query, history (list of role/content dicts).
    
    Returns: (spoken_text_response, executed_action_dict, updated_history)
    """
    # Append the user's new message to the local memory copy
    updated_history = list(history)
    updated_history.append({"role": "user", "content": query})

    action_executed = None
    max_loops = 3

    for loop_idx in range(max_loops):
        # Format history for LLM prompt
        prompt_input = query if loop_idx > 0 else f"User message: {query}"
        
        try:
            # Let's get the completion
            prompt_parts = []
            prompt_parts.append("CONVERSATION HISTORY:")
            for msg in updated_history[-10:]: # keep context to last 10 messages
                role_label = "User" if msg["role"] == "user" else "Assistant" if msg["role"] == "assistant" else "System"
                prompt_parts.append(f"{role_label}: {msg['content']}")
            
            prompt_parts.append("\nRespond to the user's latest message. Remember instructions to keep it short and spoken-friendly, or use [COMMAND: ...] if needed.")
            
            final_prompt = "\n".join(prompt_parts)
            
            response = generate_chat_answer(
                prompt=final_prompt,
                model_name=model_name,
                system_instruction=VOICE_AGENT_SYSTEM_PROMPT
            )
        except Exception as e:
            response = f"I apologize, but I ran into an issue communicating with the AI. Error details: {str(e)}"

        # Clean response
        response = response.strip()

        # Check for command match
        command_match = re.search(r'\[COMMAND:\s*(\{.*?\})\s*\]', response)
        if command_match:
            try:
                command_str = command_match.group(1)
                command_dict = json.loads(command_str)
                action_executed = command_dict

                # Execute action
                system_result = execute_agent_action(command_dict)
                
                # Append command run to history
                updated_history.append({"role": "assistant", "content": response})
                updated_history.append({"role": "system", "content": f"[SYSTEM RESULT: {system_result}]"})
                
                # Print debug info
                print(f"[Voice Agent Loop] Executed command: {command_str}. Result: {system_result}")
                # Continue loop to let LLM formulate final voice response based on result
                continue
            except Exception as e:
                response = f"I parsed a command but failed to run it. Error: {str(e)}"
                updated_history.append({"role": "assistant", "content": response})
                break
        else:
            # No command, it is the final conversational response
            updated_history.append({"role": "assistant", "content": response})
            break

    # Extract spoken response (strip out COMMAND tag so browser doesn't speak it)
    spoken_response = re.sub(r'\[COMMAND:.*?\]', '', response).strip()
    return spoken_response, action_executed, updated_history
