from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from supabase import create_client
from dotenv import load_dotenv
from werkzeug.utils import secure_filename
from functools import wraps
from datetime import datetime, timezone
import os
import hashlib
import uuid
import base64
import google.generativeai as genai

# 1. DAPAT MAUNA ITO BAGO ANG LAHAT!
load_dotenv()

# 2. DITO MO ILALAGAY YUNG PAGKUHA NG API KEY (Para nabasa na yung .env)
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

# 3. Setup Gemini Model
generation_config = {
  "temperature": 0.2,
  "top_p": 0.95,
  "top_k": 64,
  "max_output_tokens": 8192,
  "response_mime_type": "text/plain",
}
gemini_model = genai.GenerativeModel(
  model_name="gemini-2.5-flash-lite",
  generation_config=generation_config,
)

# 4. Saka pa lang magse-setup ng Flask at Supabase
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "supporttrack-secret-2024")

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("admin_logged_in"):
            return redirect(url_for("admin_login_page"))
        return f(*args, **kwargs)
    return decorated


# ==========================================
# NOTIFICATION HELPER
# ==========================================

def _create_notification(concern_id, notif_type, message):
    """
    Looks up student_id from concerns table using concern_id,
    then inserts a notification row. Safe — will not crash even if concern not found.
    """
    try:
        res = supabase.table("concerns").select("student_id").eq("id", concern_id).execute()
        if not res.data:
            print(f"[NOTIF] concern {concern_id} not found, skipping notification.")
            return
        student_id = res.data[0].get("student_id")
        if not student_id:
            print(f"[NOTIF] concern {concern_id} has no student_id, skipping.")
            return

        supabase.table("notifications").insert({
            "student_id": student_id,
            "concern_id": concern_id,
            "type":       notif_type,
            "message":    message,
            "is_read":    False
        }).execute()
        print(f"[NOTIF] Created '{notif_type}' notification for student {student_id} (concern {concern_id})")
    except Exception as e:
        print(f"[NOTIF ERROR] {e}")


# ==========================================
# SLA AUTO-ESCALATION
# ==========================================

def check_and_escalate_sla():
    """
    Auto-escalates concerns that breached SLA thresholds:
    - Routed > 2 days  → Escalated
    - Read   > 5 days  → Escalated
    Called on every /admin/concerns load.
    """
    try:
        now = datetime.now(timezone.utc)
        res = supabase.table("concerns").select("id, status, routed_at, read_at").in_(
            "status", ["Routed", "Read"]
        ).execute()

        for c in (res.data or []):
            cid    = c["id"]
            status = c["status"]
            try:
                if status == "Routed" and c.get("routed_at"):
                    routed_at = datetime.fromisoformat(c["routed_at"].replace("Z", "+00:00"))
                    diff_days = (now - routed_at).total_seconds() / 86400
                    if diff_days > 2:
                        supabase.table("concerns").update({
                            "status":          "Escalated",
                            "escalated_at":    now.isoformat(),
                            "escalation_reason": "SLA breached: No action within 2 days of routing."
                        }).eq("id", cid).execute()
                        _create_notification(
                            cid, "sla_escalated",
                            f"⚠️ Concern {cid} was auto-escalated due to SLA breach (no response within 2 days)."
                        )

                elif status == "Read" and c.get("read_at"):
                    read_at = datetime.fromisoformat(c["read_at"].replace("Z", "+00:00"))
                    diff_days = (now - read_at).total_seconds() / 86400
                    if diff_days > 5:
                        supabase.table("concerns").update({
                            "status":          "Escalated",
                            "escalated_at":    now.isoformat(),
                            "escalation_reason": "SLA breached: No resolution within 5 days of being read."
                        }).eq("id", cid).execute()
                        _create_notification(
                            cid, "sla_escalated",
                            f"⚠️ Concern {cid} was auto-escalated due to SLA breach (no resolution within 5 days)."
                        )
            except Exception as inner_e:
                print(f"[SLA] Error processing {cid}: {inner_e}")
    except Exception as e:
        print(f"[SLA ERROR] {e}")


# ==========================================
# AUTHENTICATION ROUTES
# ==========================================

@app.route("/")
def login_page():
    return render_template("login.html")

@app.route("/signup")
def signup_page():
    return render_template("signup.html")

@app.route("/register", methods=["POST"])
def register():
    data       = request.json
    firstname  = data.get("firstname")
    lastname   = data.get("lastname")
    student_id = data.get("student_id")
    program    = data.get("program")
    email      = data.get("email")
    password   = hash_password(data["password"])

    try:
        supabase.table("users").insert({
            "first_name": firstname,
            "last_name":  lastname,
            "student_id": student_id,
            "program":    program,
            "email":      email,
            "password":   password
        }).execute()
        return jsonify({"message": "Account created successfully", "status": "success"})
    except Exception as e:
        return jsonify({"message": f"Error: {str(e)}", "status": "error"})


@app.route("/login", methods=["POST"])
def login():
    data     = request.json
    email    = data["email"]
    password = hash_password(data["password"])

    res = supabase.table("users").select("*").eq("email", email).eq("password", password).execute()

    if res.data:
        user = res.data[0]
        return jsonify({
            "message":    "Login success",
            "status":     "success",
            "student_id": user.get("student_id"),
            "program":    user.get("program")
        })
    else:
        return jsonify({"message": "Invalid credentials", "status": "error"})


# ==========================================
# STUDENT ROUTES
# ==========================================

@app.route("/student_dashboard")
def student_dashboard():
    return render_template("student_dashboard.html")


@app.route("/get_user_info", methods=["POST"])
def get_user_info():
    data       = request.json
    student_id = data.get("student_id")

    if not student_id:
        return jsonify({"status": "error", "message": "No student_id provided"})

    try:
        res = supabase.table("users").select(
            "first_name, last_name, program, profile_photo"
        ).eq("student_id", student_id).execute()

        if res.data:
            user = res.data[0]
            return jsonify({
                "status":        "success",
                "first_name":    user.get("first_name", ""),
                "last_name":     user.get("last_name", ""),
                "program":       user.get("program", ""),
                "profile_photo": user.get("profile_photo")
            })
        return jsonify({"status": "error", "message": "User not found"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})


@app.route("/upload_profile_photo", methods=["POST"])
def upload_profile_photo():
    data       = request.json
    student_id = data.get("student_id")
    image_data = data.get("image_data")

    if not student_id or not image_data:
        return jsonify({"status": "error", "message": "Missing data"})

    try:
        if "," in image_data:
            header, b64 = image_data.split(",", 1)
            mime = "image/jpeg"
            if "png" in header:  mime = "image/png"
            elif "webp" in header: mime = "image/webp"
        else:
            b64  = image_data
            mime = "image/jpeg"

        file_bytes = base64.b64decode(b64)
        filename   = f"avatars/{student_id}.jpg"

        try:
            supabase.storage.from_("avatars").remove([filename])
        except Exception:
            pass

        supabase.storage.from_("avatars").upload(
            filename,
            file_bytes,
            {"content-type": mime, "upsert": "true"}
        )

        public_url = supabase.storage.from_("avatars").get_public_url(filename)

        supabase.table("users").update(
            {"profile_photo": public_url}
        ).eq("student_id", student_id).execute()

        return jsonify({"status": "success", "url": public_url})

    except Exception as e:
        print(f"Photo upload error: {e}")
        return jsonify({"status": "error", "message": str(e)})


@app.route("/remove_profile_photo", methods=["POST"])
def remove_profile_photo():
    data       = request.json
    student_id = data.get("student_id")

    if not student_id:
        return jsonify({"status": "error", "message": "Missing student_id"})

    try:
        filename = f"avatars/{student_id}.jpg"
        try:
            supabase.storage.from_("avatars").remove([filename])
        except Exception:
            pass

        supabase.table("users").update(
            {"profile_photo": None}
        ).eq("student_id", student_id).execute()

        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})


@app.route("/submit_concern", methods=["POST"])
def submit_concern():
    student_id   = request.form.get("student_id")
    program      = request.form.get("program")
    category     = request.form.get("category")
    description  = request.form.get("description")
    is_anonymous = str(request.form.get("is_anonymous")).lower() == "true"

    if category == "Academic":
        assigned_dept = "Registrar / Dean's Office"
    elif category == "Financial":
        assigned_dept = "Accounting Department"
    elif category == "Technical Support":
        assigned_dept = "MIS Department"
    elif category in ["Student Welfare", "Facilities & Welfare", "Welfare"]:
        assigned_dept = "OSA"
    else:
        assigned_dept = "General Support"

    attachment_path = None
    now = datetime.now(timezone.utc).isoformat()

    try:
        if "attachment" in request.files:
            file = request.files["attachment"]
            if file and file.filename != "":
                filename        = secure_filename(file.filename)
                unique_filename = f"{uuid.uuid4()}_{filename}"
                file_content    = file.read()
                supabase.storage.from_("attachments").upload(
                    unique_filename, file_content, {"content-type": file.content_type}
                )
                attachment_path = supabase.storage.from_("attachments").get_public_url(unique_filename)

        res_count     = supabase.table("concerns").select("id", count="exact").eq("category", category).execute()
        current_count = res_count.count if res_count.count is not None else 0
        next_number   = current_count + 1
        formatted_num = str(next_number).zfill(3)
        prefixes      = {"Academic": "ACAD", "Financial": "FINA", "Student Welfare": "WELF", "Facilities & Welfare": "WELF", "Technical Support": "TECH"}
        custom_id     = f"{prefixes.get(category, 'GEN')}-{formatted_num}"

        supabase.table("concerns").insert({
            "id":              custom_id,
            "student_id":      student_id,
            "program":         program,
            "category":        category,
            "description":     description,
            "is_anonymous":    is_anonymous,
            "assigned_dept":   assigned_dept,
            "attachment_path": attachment_path,
            "status":          "Routed",
            "routed_at":       now
        }).execute()

        actor_name = "Anonymous Student" if is_anonymous else student_id
        supabase.table("audit_logs").insert({
            "concern_id": custom_id,
            "actor":      actor_name,
            "action":     f"Submitted {custom_id} and Auto-Routed to {assigned_dept}"
        }).execute()

        # Notification: submitted
        if not is_anonymous:
            _create_notification(
                custom_id, "submitted",
                f"📨 Your concern ({custom_id}) has been submitted and routed to {assigned_dept}."
            )

        return jsonify({"status": "success", "tracking_id": custom_id})

    except Exception as e:
        print(f"Error: {e}")
        return jsonify({"message": str(e), "status": "error"})


# ==========================================
# NOTIFICATION ROUTES
# ==========================================

@app.route("/api/notifications", methods=["POST"])
def get_notifications():
    data       = request.json
    student_id = data.get("student_id")

    if not student_id:
        return jsonify({"status": "error", "message": "No student_id provided"})

    try:
        res = supabase.table("notifications").select("*") \
            .eq("student_id", student_id) \
            .order("created_at", desc=True) \
            .limit(30) \
            .execute()

        notifications = res.data or []
        unread_count  = sum(1 for n in notifications if not n.get("is_read"))

        return jsonify({
            "status":        "success",
            "notifications": notifications,
            "unread_count":  unread_count
        })
    except Exception as e:
        print(f"[NOTIF GET ERROR] {e}")
        return jsonify({"status": "error", "message": str(e)})


@app.route("/api/notifications/mark_read", methods=["POST"])
def mark_notifications_read():
    data       = request.json
    student_id = data.get("student_id")
    notif_id   = data.get("notif_id")   # Optional: if provided, marks only that one

    if not student_id:
        return jsonify({"status": "error", "message": "No student_id provided"})

    try:
        if notif_id:
            supabase.table("notifications").update({"is_read": True}) \
                .eq("id", notif_id) \
                .eq("student_id", student_id) \
                .execute()
        else:
            supabase.table("notifications").update({"is_read": True}) \
                .eq("student_id", student_id) \
                .eq("is_read", False) \
                .execute()
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})


# ==========================================
# ADMIN AUTH
# ==========================================

@app.route("/admin/login")
def admin_login_page():
    if session.get("admin_logged_in"):
        return redirect(url_for("admin_dashboard"))
    return render_template("admin_login.html")

@app.route("/admin/login", methods=["POST"])
def admin_login():
    data     = request.json
    username = data.get("username", "").strip()
    password = hash_password(data.get("password", ""))

    try:
        res = supabase.table("admins") \
            .select("*") \
            .eq("username", username) \
            .eq("password", password) \
            .execute()

        if res.data:
            admin = res.data[0]
            session["admin_logged_in"] = True
            session["admin_username"]  = admin["username"]
            session["admin_role"]      = admin["role"]
            session["admin_dept"]      = admin.get("assigned_dept", "ALL")
            return jsonify({
                "status":   "success",
                "username": admin["username"],
                "role":     admin["role"],
                "dept":     admin.get("assigned_dept", "ALL")
            })
        return jsonify({"status": "error", "message": "Invalid username or password."})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@app.route("/admin/logout")
def admin_logout():
    session.clear()
    return redirect(url_for("admin_login_page"))


# ==========================================
# ADMIN DASHBOARD (protected)
# ==========================================

@app.route("/admin")
@admin_required
def admin_dashboard():
    return render_template("admin_dashboard.html")

@app.route("/admin/concerns", methods=["GET"])
@admin_required
def admin_get_concerns():
    try:
        # Run SLA check first
        check_and_escalate_sla()

        admin_dept = session.get("admin_dept", "ALL")

        if admin_dept == "ALL" or session.get("admin_role") == "superadmin":
            res = supabase.table("concerns").select("*").order("created_at", desc=True).execute()
        else:
            res = supabase.table("concerns").select("*").eq("assigned_dept", admin_dept).order("created_at", desc=True).execute()

        return jsonify({"status": "success", "concerns": res.data})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})


@app.route("/admin/update_status", methods=["POST"])
@admin_required
def admin_update_status():
    data       = request.json
    concern_id = data.get("concern_id")
    new_status = data.get("status")
    now        = datetime.now(timezone.utc).isoformat()

    allowed = ["Routed", "Read", "Screened", "Resolved", "Escalated", "Closed"]
    if new_status not in allowed:
        return jsonify({"status": "error", "message": "Invalid status value."})

    # Build update payload — stamp the right timestamp
    update_payload = {"status": new_status}
    if new_status == "Routed":
        update_payload["routed_at"] = now
    elif new_status == "Read":
        update_payload["read_at"] = now
    elif new_status == "Screened":
        update_payload["screened_at"] = now
    elif new_status == "Resolved":
        update_payload["resolved_at"] = now
    elif new_status == "Escalated":
        update_payload["escalated_at"] = now
        update_payload["escalation_reason"] = data.get("escalation_reason", "Manually escalated by admin.")

    try:
        supabase.table("concerns").update(update_payload).eq("id", concern_id).execute()

        supabase.table("audit_logs").insert({
            "concern_id": concern_id,
            "actor":      session.get("admin_username", "Admin"),
            "action":     f"Status updated to '{new_status}' for concern {concern_id}"
        }).execute()

        # --- NOTIFICATION per status ---
        notif_map = {
            "Read":      ("read",      f"👁️ Your concern ({concern_id}) has been read by the admin."),
            "Screened":  ("screened",  f"🔍 Your concern ({concern_id}) is now being screened."),
            "Resolved":  ("resolved",  f"✅ Your concern ({concern_id}) has been resolved. Thank you for reaching out!"),
            "Escalated": ("escalated", f"⚠️ Your concern ({concern_id}) has been escalated for further review."),
            "Closed":    ("closed",    f"🔒 Your concern ({concern_id}) has been closed."),
            "Routed":    ("routed",    f"📨 Your concern ({concern_id}) has been re-routed."),
        }

        if new_status in notif_map:
            ntype, nmsg = notif_map[new_status]
            _create_notification(concern_id, ntype, nmsg)

        return jsonify({"status": "success"})
    except Exception as e:
        print(f"[UPDATE STATUS ERROR] {e}")
        return jsonify({"status": "error", "message": str(e)})


@app.route("/admin/metrics", methods=["GET"])
@admin_required
def admin_metrics():
    try:
        res = supabase.table("concerns").select("*").execute()
        concerns = res.data or []

        total      = len(concerns)
        resolved   = [c for c in concerns if c.get("status") == "Resolved"]
        escalated  = [c for c in concerns if c.get("status") == "Escalated"]

        # Avg response time (routed_at → read_at), in hours
        response_times = []
        for c in concerns:
            if c.get("routed_at") and c.get("read_at"):
                try:
                    r = datetime.fromisoformat(c["routed_at"].replace("Z", "+00:00"))
                    d = datetime.fromisoformat(c["read_at"].replace("Z", "+00:00"))
                    response_times.append((d - r).total_seconds() / 3600)
                except Exception:
                    pass

        # Avg resolution time (routed_at → resolved_at), in hours
        resolution_times = []
        for c in resolved:
            if c.get("routed_at") and c.get("resolved_at"):
                try:
                    r  = datetime.fromisoformat(c["routed_at"].replace("Z", "+00:00"))
                    rs = datetime.fromisoformat(c["resolved_at"].replace("Z", "+00:00"))
                    resolution_times.append((rs - r).total_seconds() / 3600)
                except Exception:
                    pass

        avg_response   = round(sum(response_times)   / len(response_times),   1) if response_times   else 0
        avg_resolution = round(sum(resolution_times) / len(resolution_times), 1) if resolution_times else 0
        escalation_rate = round(len(escalated) / total * 100, 1) if total else 0
        resolution_rate = round(len(resolved)  / total * 100, 1) if total else 0

        # Breakdowns
        from collections import Counter
        by_dept     = dict(Counter(c.get("assigned_dept", "Unknown") for c in concerns))
        by_category = dict(Counter(c.get("category",      "Unknown") for c in concerns))
        by_status   = dict(Counter(c.get("status",        "Unknown") for c in concerns))

        # 7-day daily submissions
        from datetime import timedelta
        now   = datetime.now(timezone.utc)
        daily = {}
        for i in range(6, -1, -1):
            day = (now - timedelta(days=i)).strftime("%b %d")
            daily[day] = 0
        for c in concerns:
            if c.get("created_at"):
                try:
                    dt  = datetime.fromisoformat(c["created_at"].replace("Z", "+00:00"))
                    day = dt.strftime("%b %d")
                    if day in daily:
                        daily[day] += 1
                except Exception:
                    pass

        return jsonify({
            "status":           "success",
            "total":            total,
            "avg_response_hrs": avg_response,
            "avg_resolution_hrs": avg_resolution,
            "escalation_rate":  escalation_rate,
            "resolution_rate":  resolution_rate,
            "by_dept":          by_dept,
            "by_category":      by_category,
            "by_status":        by_status,
            "daily_submissions": daily
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})


@app.route("/admin/audit_logs", methods=["GET"])
@admin_required
def admin_get_audit_logs():
    try:
        res = supabase.table("audit_logs").select("*").order("created_at", desc=True).execute()
        return jsonify({"status": "success", "logs": res.data})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@app.route("/admin/students", methods=["GET"])
@admin_required
def admin_get_students():
    try:
        res = supabase.table("users").select(
            "student_id, first_name, last_name, program, email, created_at"
        ).order("created_at", desc=True).execute()
        return jsonify({"status": "success", "students": res.data})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@app.route("/admin/student_info/<student_id>", methods=["GET"])
@admin_required
def admin_get_student_info(student_id):
    try:
        res = supabase.table("users").select(
            "first_name, last_name"
        ).eq("student_id", student_id).execute()
        if res.data:
            user = res.data[0]
            return jsonify({
                "status":    "success",
                "full_name": user["first_name"] + " " + user["last_name"]
            })
        return jsonify({"status": "not_found", "full_name": "Unknown"})
    except Exception as e:
        return jsonify({"status": "error", "full_name": "Unknown"})


@app.route("/admin/get_admins", methods=["GET"])
@admin_required
def admin_get_admins():
    try:
        res = supabase.table("admins").select("id, username, email, full_name, role, assigned_dept").execute()
        return jsonify({"status": "success", "admins": res.data})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@app.route("/admin/add_admin", methods=["POST"])
@admin_required
def admin_add_admin():
    if session.get("admin_role") != "superadmin":
        return jsonify({"status": "error", "message": "Unauthorized. Super Admin only."})

    data = request.json
    try:
        supabase.table("admins").insert({
            "username":     data["username"],
            "email":        data["email"],
            "full_name":    data["full_name"],
            "password":     hash_password(data["password"]),
            "role":         data["role"],
            "assigned_dept": data["assigned_dept"]
        }).execute()

        supabase.table("audit_logs").insert({
            "actor":  session.get("admin_username", "Admin"),
            "action": f"Created new admin account for {data['username']} ({data['assigned_dept']})"
        }).execute()

        return jsonify({"status": "success", "message": "Admin added successfully!"})
    except Exception as e:
        return jsonify({"status": "error", "message": "Error adding admin. Username or email might exist."})

@app.route("/admin/delete_admin/<admin_id>", methods=["DELETE"])
@admin_required
def admin_delete_admin(admin_id):
    if session.get("admin_role") != "superadmin":
        return jsonify({"status": "error", "message": "Unauthorized. Super Admin only."})

    try:
        res = supabase.table("admins").select("username").eq("id", admin_id).execute()
        if res.data and res.data[0]["username"] == "admin":
            return jsonify({"status": "error", "message": "Cannot delete the default superadmin account."})

        supabase.table("admins").delete().eq("id", admin_id).execute()

        supabase.table("audit_logs").insert({
            "actor":  session.get("admin_username", "Admin"),
            "action": "Deleted an admin account"
        }).execute()

        return jsonify({"status": "success", "message": "Admin deleted successfully."})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@app.route("/admin/update_admin", methods=["POST"])
@admin_required
def admin_update_admin():
    if session.get("admin_role") != "superadmin":
        return jsonify({"status": "error", "message": "Unauthorized. Super Admin only."})

    data     = request.json
    admin_id = data.get("id")

    try:
        update_data = {
            "username":      data["username"],
            "full_name":     data["username"],
            "email":         data["email"],
            "role":          data["role"],
            "assigned_dept": data["assigned_dept"]
        }

        if data.get("password") and data["password"].strip() != "":
            update_data["password"] = hash_password(data["password"])

        supabase.table("admins").update(update_data).eq("id", admin_id).execute()

        supabase.table("audit_logs").insert({
            "actor":  session.get("admin_username", "Admin"),
            "action": f"Updated admin account: {data['username']}"
        }).execute()

        return jsonify({"status": "success", "message": "Admin updated successfully."})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})


# ==========================================
# PUBLIC / GEMINI ROUTES
# ==========================================

@app.route("/api/public_concerns", methods=["GET"])
def get_public_concerns():
    try:
        res = supabase.table("concerns").select("*").order("created_at", desc=True).execute()
        return jsonify({"status": "success", "concerns": res.data})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})


@app.route("/api/global_ai_chat", methods=["POST"])
def global_ai_chat():
    data         = request.json
    user_message = data.get("message", "")

    system_prompt = """
    You are 'Global AI', the official AI assistant of Global Reciprocal College (GRC). 
    Your role is to provide quick and concise departmental routing for student concerns.

    ROUTING RULES:
    - Registrar / Dean's Office: Grades, subjects, records, shifting/dropping.
    - Accounting Department: Tuition, balances, scholarships, payments.
    - OSA: Bullying, lost items, facilities (aircon/chairs), student orgs.
    - MIS Department: Portal access, password resets, Wi-Fi, tech issues.

    BEHAVIOR:
    - Keep responses short, direct, and professional.
    - Do not over-explain. Just tell them where to go or ask a quick follow-up if unclear.
    - Understand Tagalog/Taglish but respond in clear, simple English unless Tagalog is necessary for clarity.
    - Strictly school-related only. No code. No unrelated topics.
    """

    try:
        chat_session = gemini_model.start_chat(history=[])
        response     = chat_session.send_message(f"{system_prompt}\n\nStudent: {user_message}")
        return jsonify({"status": "success", "reply": response.text})
    except Exception as e:
        print(f"\n=====================================")
        print(f"GEMINI ERROR: {str(e)}")
        print(f"=====================================\n")
        return jsonify({"status": "error", "message": "Global AI is currently resting. Please try again later."})


@app.route("/api/moderate_concern", methods=["POST"])
def moderate_concern():
    data        = request.json
    description = data.get("description", "")
    category    = data.get("category", "")

    prompt = f"""
    Read the student concern description below.

    The student selected this category: "{category}"

    Determine if it should be PASSED or REJECTED based on these rules:
    1. Does it contain profanity, offensive language, or inappropriate words? (If yes, REJECTED)
    2. Is it nonsense or just random characters (e.g. "asdasd", "hello world")? (If yes, REJECTED)
    3. Does the description actually match the selected category?
    - Academic = grades, subjects, enrollment, shifting, dropping, professors, schedule
    - Financial = tuition, balance, scholarship, payment, fees
    - Student Welfare = bullying, harassment, mental health, lost items, facilities
    - Technical Support = portal issues, password, wifi, system errors
    If the description is clearly unrelated to the selected category, (REJECTED)

    If everything is valid, respond with 'PASSED'.
    If it violates any rule, respond with 'REJECTED: <one professional English sentence explaining why>'.

    Description: "{description}"
    """

    try:
        response = gemini_model.generate_content(prompt)
        reply    = response.text.strip()

        if reply.startswith("PASSED"):
            return jsonify({"status": "success", "action": "allow"})
        else:
            reason = reply.replace("REJECTED:", "").strip()
            if not reason: reason = "Inappropriate or irrelevant content detected."
            return jsonify({"status": "success", "action": "block", "reason": reason})

    except Exception as e:
        return jsonify({"status": "success", "action": "allow"})


# ==========================================
# RUN
# ==========================================
if __name__ == '__main__':
    app.run(debug=True)