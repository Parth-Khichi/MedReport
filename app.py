import os
import csv
import re
import json
import uuid
import difflib
import sqlite3
import zipfile
from functools import wraps

import pymupdf

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    session,
    flash,
    send_file,
    abort
)

from werkzeug.security import (
    generate_password_hash,
    check_password_hash
)

from werkzeug.utils import secure_filename

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib import colors

from database import get_db, init_db


# ============================================================
# APPLICATION
# ============================================================

app = Flask(__name__)

app.secret_key = os.environ.get(
    "SECRET_KEY",
    "change-this-secret-key-in-production"
)

# Maximum upload size = 15 MB
app.config["MAX_CONTENT_LENGTH"] = 15 * 1024 * 1024


# ============================================================
# DIRECTORIES
# ============================================================

UPLOAD_FOLDER = "uploads"
GENERATED_FOLDER = "generated_reports"

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(GENERATED_FOLDER, exist_ok=True)


# ============================================================
# ALLOWED FILE TYPES
# ============================================================

ALLOWED_EXTENSIONS = {
    "pdf",
    "png",
    "jpg",
    "jpeg"
}


def allowed_file(filename):

    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower()
        in ALLOWED_EXTENSIONS
    )


# ============================================================
# DATABASE
# ============================================================

init_db()


# ============================================================
# LOGIN REQUIRED
# ============================================================

def login_required(function):

    @wraps(function)
    def wrapper(*args, **kwargs):

        if "user_id" not in session:
            return redirect(url_for("login"))

        return function(*args, **kwargs)

    return wrapper


# ============================================================
# HOME
# ============================================================

@app.route("/")
def index():

    if "user_id" in session:
        return redirect(url_for("dashboard"))

    return redirect(url_for("login"))


# ============================================================
# REGISTER
# ============================================================

@app.route("/register", methods=["GET", "POST"])
def register():

    if request.method == "POST":

        name = request.form.get(
            "name",
            ""
        ).strip()

        email = request.form.get(
            "email",
            ""
        ).strip().lower()

        password = request.form.get(
            "password",
            ""
        )

        if not name or not email or not password:

            flash(
                "Please fill all fields.",
                "error"
            )

            return redirect(
                url_for("register")
            )

        if len(password) < 6:

            flash(
                "Password must contain at least 6 characters.",
                "error"
            )

            return redirect(
                url_for("register")
            )

        hashed_password = generate_password_hash(
            password
        )

        conn = get_db()

        try:

            conn.execute(
                """
                INSERT INTO users
                (name, email, password)
                VALUES (?, ?, ?)
                """,
                (
                    name,
                    email,
                    hashed_password
                )
            )

            conn.commit()

            flash(
                "Registration successful. Please login.",
                "success"
            )

            return redirect(
                url_for("login")
            )

        except sqlite3.IntegrityError:

            flash(
                "Email already registered.",
                "error"
            )

            return redirect(
                url_for("register")
            )

        finally:
            conn.close()

    return render_template(
        "register.html"
    )


# ============================================================
# LOGIN
# ============================================================

@app.route("/login", methods=["GET", "POST"])
def login():

    if request.method == "POST":

        email = request.form.get(
            "email",
            ""
        ).strip().lower()

        password = request.form.get(
            "password",
            ""
        )

        conn = get_db()

        user = conn.execute(
            """
            SELECT *
            FROM users
            WHERE email = ?
            """,
            (email,)
        ).fetchone()

        conn.close()

        if (
            user
            and check_password_hash(
                user["password"],
                password
            )
        ):

            session.clear()

            session["user_id"] = user["id"]
            session["user_name"] = user["name"]

            return redirect(
                url_for("dashboard")
            )

        flash(
            "Invalid email or password.",
            "error"
        )

    return render_template(
        "login.html"
    )


# ============================================================
# LOGOUT
# ============================================================

@app.route("/logout")
def logout():

    session.clear()

    return redirect(
        url_for("login")
    )


# ============================================================
# DASHBOARD
# ============================================================

@app.route("/dashboard")
@login_required
def dashboard():

    return render_template(
        "dashboard.html",
        user_name=session["user_name"]
    )


# ============================================================
# FILE EXTRACTION
# ============================================================

def extract_from_pdf(path):

    """
    Extract text from a normal text-based PDF.

    This does NOT use OCR.

    If the PDF is scanned/image-only, this function
    will return an empty string.
    """

    text_parts = []

    document = None

    try:

        document = pymupdf.open(path)

        for page in document:

            text = page.get_text("text")

            if text and text.strip():

                text_parts.append(
                    text.strip()
                )

        return "\n\n".join(
            text_parts
        ).strip()

    finally:

        if document is not None:
            document.close()


def extract_report_text(path):

    """
    Currently supports text extraction from PDFs.

    Images and scanned PDFs require OCR, which is intentionally
    not included because the project is being configured without
    Tesseract.
    """

    extension = path.rsplit(
        ".",
        1
    )[1].lower()

    if extension == "pdf":

        return extract_from_pdf(path)

    return ""


# ============================================================
# CHECK WHETHER PDF CONTAINS TEXT
# ============================================================

def pdf_contains_text(path):

    try:

        text = extract_from_pdf(path)

        return bool(
            text.strip()
        )

    except Exception as error:

        print(
            "PDF TEXT CHECK ERROR:",
            error
        )

        return False


# ============================================================
# TEXT NORMALIZATION
# ============================================================

def normalize_text(text):

    text = text.lower()

    text = re.sub(
        r"[^a-z0-9\s]",
        " ",
        text
    )

    text = re.sub(
        r"\s+",
        " ",
        text
    )

    return text.strip()


# ============================================================
# LAB VALUE ANALYSIS
# ============================================================


def analyze_report(text):

    results = []

    normalized_text = text.lower()

    # ============================================================
    # TEST DEFINITIONS
    #
    # The system looks for:
    #
    # Test Name + Result + Reference Range + Unit
    #
    # Example:
    #
    # Haemoglobin 15 13 - 17 g/dL
    #
    # ============================================================

    tests = [

        {
            "name": "Hemoglobin",
            "patterns": [
                r"haemoglobin\s+([0-9]+(?:\.[0-9]+)?)\s+"
                r"([0-9]+(?:\.[0-9]+)?)\s*-\s*"
                r"([0-9]+(?:\.[0-9]+)?)\s*"
                r"(g/dl|gm/dl)"
            ]
        },

        {
            "name": "Total Leucocyte Count",
            "patterns": [
                r"total\s+leucocyte\s+count\s+"
                r"([0-9]+(?:\.[0-9]+)?)\s+"
                r"([0-9]+(?:\.[0-9]+)?)\s*-\s*"
                r"([0-9]+(?:\.[0-9]+)?)\s*"
                r"(/cumm|/cmm)"
            ]
        },

        {
            "name": "Neutrophils",
            "patterns": [
                r"neutrophils\s+"
                r"([0-9]+(?:\.[0-9]+)?)\s+"
                r"([0-9]+(?:\.[0-9]+)?)\s*-\s*"
                r"([0-9]+(?:\.[0-9]+)?)\s*%"
            ]
        },

        {
            "name": "Lymphocytes",
            "patterns": [
                r"lymphocytes\s+"
                r"([0-9]+(?:\.[0-9]+)?)\s+"
                r"([0-9]+(?:\.[0-9]+)?)\s*-\s*"
                r"([0-9]+(?:\.[0-9]+)?)\s*%"
            ]
        },

        {
            "name": "Eosinophils",
            "patterns": [
                r"eosinophils\s+"
                r"([0-9]+(?:\.[0-9]+)?)\s+"
                r"([0-9]+(?:\.[0-9]+)?)\s*-\s*"
                r"([0-9]+(?:\.[0-9]+)?)\s*%"
            ]
        },

        {
            "name": "Monocytes",
            "patterns": [
                r"monocytes\s+"
                r"([0-9]+(?:\.[0-9]+)?)\s+"
                r"([0-9]+(?:\.[0-9]+)?)\s*-\s*"
                r"([0-9]+(?:\.[0-9]+)?)\s*%"
            ]
        },

        {
            "name": "Basophils",
            "patterns": [
                r"basophils\s+"
                r"([0-9]+(?:\.[0-9]+)?)\s+"
                r"([0-9]+(?:\.[0-9]+)?)\s*-\s*"
                r"([0-9]+(?:\.[0-9]+)?)\s*%"
            ]
        },

        {
            "name": "MCV",
            "patterns": [
                r"\bmcv\s+"
                r"([0-9]+(?:\.[0-9]+)?)\s+"
                r"([0-9]+(?:\.[0-9]+)?)\s*-\s*"
                r"([0-9]+(?:\.[0-9]+)?)\s*"
                r"(fl)"
            ]
        },

        {
            "name": "MCH",
            "patterns": [
                r"\bmch\s+"
                r"([0-9]+(?:\.[0-9]+)?)\s+"
                r"([0-9]+(?:\.[0-9]+)?)\s*-\s*"
                r"([0-9]+(?:\.[0-9]+)?)\s*"
                r"(pg)"
            ]
        },

        {
            "name": "MCHC",
            "patterns": [
                r"\bmchc\s+"
                r"([0-9]+(?:\.[0-9]+)?)\s+"
                r"([0-9]+(?:\.[0-9]+)?)\s*-\s*"
                r"([0-9]+(?:\.[0-9]+)?)\s*"
                r"(g/dl)"
            ]
        },

        {
            "name": "Hematocrit",
            "patterns": [
                r"(?:hct|hematocrit)\s+"
                r"([0-9]+(?:\.[0-9]+)?)\s+"
                r"([0-9]+(?:\.[0-9]+)?)\s*-\s*"
                r"([0-9]+(?:\.[0-9]+)?)\s*%"
            ]
        },

        {
            "name": "RDW-CV",
            "patterns": [
                r"rdw-cv\s+"
                r"([0-9]+(?:\.[0-9]+)?)\s+"
                r"([0-9]+(?:\.[0-9]+)?)\s*-\s*"
                r"([0-9]+(?:\.[0-9]+)?)\s*%"
            ]
        },

        {
            "name": "RDW-SD",
            "patterns": [
                r"rdw-sd\s+"
                r"([0-9]+(?:\.[0-9]+)?)\s+"
                r"([0-9]+(?:\.[0-9]+)?)\s*-\s*"
                r"([0-9]+(?:\.[0-9]+)?)\s*"
                r"(fl)"
            ]
        },

        {
            "name": "Platelet Count",
            "patterns": [
                r"platelet\s+count\s+"
                r"([0-9]+(?:\.[0-9]+)?)\s+"
                r"([0-9]+(?:\.[0-9]+)?)\s*-\s*"
                r"([0-9]+(?:\.[0-9]+)?)\s*"
                r"(/cumm|/cmm)"
            ]
        },

        {
            "name": "MPV",
            "patterns": [
                r"\bmpv\s+"
                r"([0-9]+(?:\.[0-9]+)?)\s+"
                r"([0-9]+(?:\.[0-9]+)?)\s*-\s*"
                r"([0-9]+(?:\.[0-9]+)?)\s*"
                r"(fl)"
            ]
        }

    ]


    # ============================================================
    # FIND TEST VALUES
    # ============================================================

    for test in tests:

        found = False

        for pattern in test["patterns"]:

            match = re.search(
                pattern,
                normalized_text,
                re.IGNORECASE
            )

            if not match:
                continue

            try:

                value = float(
                    match.group(1)
                )

                reference_low = float(
                    match.group(2)
                )

                reference_high = float(
                    match.group(3)
                )

                unit = match.group(
                    4
                )

            except (
                ValueError,
                IndexError
            ):

                continue


            # ====================================================
            # DETERMINE STATUS
            # ====================================================

            if value < reference_low:

                status = "Low"

            elif value > reference_high:

                status = "High"

            else:

                status = "Normal"


            # ====================================================
            # BASIC EXPLANATION
            # ====================================================

            explanation = get_test_explanation(
                test["name"],
                status
            )


            results.append(
                {
                    "name": test["name"],
                    "value": value,
                    "reference_low": reference_low,
                    "reference_high": reference_high,
                    "unit": unit,
                    "status": status,
                    "explanation": explanation
                }
            )

            found = True

            break


    # ============================================================
    # SUMMARY
    # ============================================================

    normal_count = 0
    low_count = 0
    high_count = 0

    for result in results:

        if result["status"] == "Normal":
            normal_count += 1

        elif result["status"] == "Low":
            low_count += 1

        elif result["status"] == "High":
            high_count += 1


    if not results:

        summary = (
            "The report text was extracted successfully, "
            "but the system could not confidently identify "
            "supported laboratory parameters and their "
            "reference ranges."
        )

    else:

        summary = (
            f"The system identified {len(results)} "
            f"laboratory parameter(s). "
            f"{normal_count} are within the reference range, "
            f"{low_count} are below the reference range, "
            f"and {high_count} are above the reference range."
        )


    # ============================================================
    # GENERAL RECOMMENDATIONS
    # ============================================================

    recommendations = []

    abnormal_results = [
        result
        for result in results
        if result["status"] != "Normal"
    ]


    if not abnormal_results:

        recommendations.append(
            "The detected values are within the reference "
            "ranges printed on this report."
        )

        recommendations.append(
            "Continue routine healthcare and follow any "
            "advice given by your healthcare professional."
        )

    else:

        recommendations.append(
            "Review the abnormal values with a qualified "
            "healthcare professional, particularly if you "
            "have symptoms or if these results are unexpected."
        )

        recommendations.append(
            "Do not change medicines, supplements, or treatment "
            "based only on this automated analysis."
        )


    return {

        "values": results,

        "summary": summary,

        "normal_count": normal_count,

        "low_count": low_count,

        "high_count": high_count,

        "recommendations": recommendations

    }


# ============================================================
# TEST EXPLANATIONS
# ============================================================

def get_test_explanation(
    test_name,
    status
):

    explanations = {

        "Hemoglobin": {

            "Normal":
                "The hemoglobin result is within the "
                "reference range shown on this report.",

            "Low":
                "Hemoglobin is below the reference range. "
                "Low hemoglobin can have several causes, "
                "including different types of anemia. "
                "A healthcare professional should interpret "
                "this together with the other CBC results "
                "and your symptoms.",

            "High":
                "Hemoglobin is above the reference range. "
                "There are several possible causes of an "
                "elevated result, so it should be interpreted "
                "with the rest of the blood report and "
                "your clinical history."

        },


        "Total Leucocyte Count": {

            "Normal":
                "The total white blood cell count is within "
                "the reference range shown on this report.",

            "Low":
                "The white blood cell count is below the "
                "reference range. This can occur for several "
                "reasons and should be interpreted by a "
                "healthcare professional.",

            "High":
                "The white blood cell count is above the "
                "reference range. An elevated count can occur "
                "with infections, inflammation, or other "
                "conditions and requires clinical context."

        },


        "Neutrophils": {

            "Normal":
                "The neutrophil percentage is within the "
                "reference range shown on this report.",

            "Low":
                "The neutrophil percentage is below the "
                "reference range. The result should be "
                "interpreted together with the total white "
                "blood cell count and clinical history.",

            "High":
                "The neutrophil percentage is above the "
                "reference range. This can occur in several "
                "situations, including some infections or "
                "inflammatory conditions."

        },


        "Lymphocytes": {

            "Normal":
                "The lymphocyte percentage is within the "
                "reference range shown on this report.",

            "Low":
                "The lymphocyte percentage is below the "
                "reference range. This finding needs to be "
                "interpreted together with the rest of the CBC.",

            "High":
                "The lymphocyte percentage is above the "
                "reference range. There are multiple possible "
                "causes and the result should be interpreted "
                "with your clinical history."

        },


        "Eosinophils": {

            "Normal":
                "The eosinophil percentage is within the "
                "reference range shown on this report.",

            "Low":
                "The eosinophil percentage is below the "
                "reference range. This isolated result is "
                "usually interpreted together with the rest "
                "of the CBC.",

            "High":
                "The eosinophil percentage is above the "
                "reference range. Elevated eosinophils can "
                "occur in some allergic conditions and other "
                "situations and should be clinically interpreted."

        },


        "Monocytes": {

            "Normal":
                "The monocyte percentage is within the "
                "reference range shown on this report.",

            "Low":
                "The monocyte percentage is below the "
                "reference range. It should be interpreted "
                "with the complete blood count.",

            "High":
                "The monocyte percentage is above the "
                "reference range. This can have several causes "
                "and should be reviewed with the complete "
                "blood count and clinical history."

        },


        "Basophils": {

            "Normal":
                "The basophil percentage is within the "
                "reference range shown on this report.",

            "Low":
                "The basophil percentage is below the "
                "reference range.",

            "High":
                "The basophil percentage is above the "
                "reference range and should be interpreted "
                "together with the rest of the blood report."

        },


        "MCV": {

            "Normal":
                "MCV is within the reference range. MCV "
                "describes the average size of red blood cells.",

            "Low":
                "MCV is below the reference range, meaning "
                "the red blood cells are smaller than the "
                "range used by this laboratory. This can be "
                "seen in some types of anemia and should be "
                "interpreted together with hemoglobin and "
                "other RBC measurements.",

            "High":
                "MCV is above the reference range, meaning "
                "the red blood cells are larger than the "
                "range used by this laboratory. There are "
                "several possible causes and the complete "
                "blood picture should be reviewed."

        },


        "MCH": {

            "Normal":
                "MCH is within the reference range shown "
                "on this report.",

            "Low":
                "MCH is below the reference range. This "
                "measurement should be interpreted together "
                "with hemoglobin, MCV, and other red blood "
                "cell measurements.",

            "High":
                "MCH is above the reference range. The result "
                "should be interpreted together with MCV, "
                "hemoglobin, and the rest of the CBC."

        },


        "MCHC": {

            "Normal":
                "MCHC is within the reference range shown "
                "on this report.",

            "Low":
                "MCHC is below the reference range. This "
                "measurement describes the concentration of "
                "hemoglobin inside red blood cells and should "
                "be interpreted with the other RBC indices.",

            "High":
                "MCHC is above the reference range. The result "
                "should be reviewed with the other red blood "
                "cell measurements and the laboratory's "
                "reference information."

        },


        "Hematocrit": {

            "Normal":
                "Hematocrit is within the reference range "
                "shown on this report.",

            "Low":
                "Hematocrit is below the reference range. "
                "This can occur for several reasons, including "
                "some forms of anemia, and should be interpreted "
                "with hemoglobin and other CBC results.",

            "High":
                "Hematocrit is above the reference range. "
                "Several factors can affect hematocrit, so "
                "the result should be interpreted together "
                "with the complete blood count."

        },


        "RDW-CV": {

            "Normal":
                "RDW-CV is within the reference range shown "
                "on this report.",

            "Low":
                "RDW-CV is below the reference range. This "
                "result should be interpreted together with "
                "the other red blood cell indices.",

            "High":
                "RDW-CV is above the reference range. This "
                "indicates greater variation in red blood cell "
                "size and should be interpreted with the "
                "other CBC measurements."

        },


        "RDW-SD": {

            "Normal":
                "RDW-SD is within the reference range shown "
                "on this report.",

            "Low":
                "RDW-SD is below the reference range and "
                "should be interpreted together with the "
                "other RBC indices.",

            "High":
                "RDW-SD is above the reference range. This "
                "indicates increased variation in red blood "
                "cell size and should be interpreted together "
                "with the other CBC measurements."

        },


        "Platelet Count": {

            "Normal":
                "The platelet count is within the reference "
                "range shown on this report.",

            "Low":
                "The platelet count is below the reference "
                "range. Low platelets can have several causes "
                "and should be reviewed by a healthcare "
                "professional, especially if there is unusual "
                "bleeding or bruising.",

            "High":
                "The platelet count is above the reference "
                "range. There are multiple possible causes, "
                "so the result should be interpreted by a "
                "healthcare professional."

        },


        "MPV": {

            "Normal":
                "MPV is within the reference range shown "
                "on this report.",

            "Low":
                "MPV is below the reference range and should "
                "be interpreted together with the platelet count.",

            "High":
                "MPV is above the reference range and should "
                "be interpreted together with the platelet "
                "count and other CBC findings."

        }

    }


    default_message = (
        f"{test_name} is {status.lower()} compared with "
        "the reference range shown on this report. "
        "Please discuss abnormal results with a qualified "
        "healthcare professional."
    )


    return (
        explanations
        .get(
            test_name,
            {}
        )
        .get(
            status,
            default_message
        )
    )


# ============================================================
# REPORT ANALYSIS
# ============================================================

@app.route(
    "/report-analyze",
    methods=["GET", "POST"]
)
@login_required
def report_analyze():

    if request.method == "POST":

        report_name = request.form.get(
            "report_name",
            "Medical Report"
        ).strip()

        uploaded_file = request.files.get(
            "report_file"
        )

        # ----------------------------------------------------
        # CHECK FILE
        # ----------------------------------------------------

        if not uploaded_file:

            flash(
                "Please select a report file.",
                "error"
            )

            return redirect(
                url_for("report_analyze")
            )

        if not uploaded_file.filename:

            flash(
                "Please select a file.",
                "error"
            )

            return redirect(
                url_for("report_analyze")
            )

        original_filename = secure_filename(
            uploaded_file.filename
        )

        if not allowed_file(
            original_filename
        ):

            flash(
                "Only PDF, PNG, JPG and JPEG files are supported.",
                "error"
            )

            return redirect(
                url_for("report_analyze")
            )

        extension = original_filename.rsplit(
            ".",
            1
        )[1].lower()

        # ----------------------------------------------------
        # WITHOUT OCR
        # ----------------------------------------------------
        #
        # Images cannot be converted to text without OCR.
        #
        # We therefore show a clear message rather than
        # allowing the application to crash.
        # ----------------------------------------------------

        if extension in {
            "png",
            "jpg",
            "jpeg"
        }:

            flash(
                "Image reports require OCR to extract their "
                "text. Please upload a text-based PDF for now.",
                "error"
            )

            return redirect(
                url_for("report_analyze")
            )

        # ----------------------------------------------------
        # CREATE UNIQUE FILE NAME
        # ----------------------------------------------------

        unique_filename = (
            f"{uuid.uuid4().hex}.{extension}"
        )

        stored_path = os.path.join(
            UPLOAD_FOLDER,
            unique_filename
        )

        try:

            uploaded_file.save(
                stored_path
            )

            # ------------------------------------------------
            # CHECK PDF
            # ------------------------------------------------

            extracted_text = extract_report_text(
                stored_path
            )

            # ------------------------------------------------
            # SCANNED PDF DETECTION
            # ------------------------------------------------

            if not extracted_text:

                # Remove file because we cannot analyze it
                if os.path.exists(
                    stored_path
                ):
                    os.remove(
                        stored_path
                    )

                flash(
                    "This PDF appears to be scanned or "
                    "image-based. Text could not be extracted "
                    "because OCR is not installed. Please "
                    "upload a text-based PDF.",
                    "error"
                )

                return redirect(
                    url_for("report_analyze")
                )

            # ------------------------------------------------
            # ANALYZE REPORT
            # ------------------------------------------------

            analysis = analyze_report(
                extracted_text
            )

            analysis_json = json.dumps(
                analysis
            )

            # ------------------------------------------------
            # SAVE TO DATABASE
            # ------------------------------------------------

            conn = get_db()

            cursor = conn.execute(
                """
                INSERT INTO reports
                (
                    user_id,
                    report_name,
                    report_text,
                    analysis,
                    original_filename,
                    stored_filename,
                    report_type
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session["user_id"],
                    report_name,
                    extracted_text,
                    analysis_json,
                    original_filename,
                    unique_filename,
                    extension
                )
            )

            report_id = cursor.lastrowid

            conn.commit()
            conn.close()

            flash(
                "PDF uploaded and analyzed successfully.",
                "success"
            )

            return redirect(
                url_for(
                    "report_result",
                    report_id=report_id
                )
            )

        except Exception as error:

            print(
                "=" * 60
            )

            print(
                "REPORT PROCESSING ERROR:"
            )

            print(
                error
            )

            print(
                "=" * 60
            )

            if os.path.exists(
                stored_path
            ):
                os.remove(
                    stored_path
                )

            flash(
                "The PDF could not be processed. "
                "Please make sure it is a valid PDF.",
                "error"
            )

            return redirect(
                url_for("report_analyze")
            )

    return render_template(
        "report_analyze.html"
    )


# ============================================================
# VIEW REPORT RESULT
# ============================================================

@app.route(
    "/report/<int:report_id>"
)
@login_required
def report_result(report_id):

    conn = get_db()

    report = conn.execute(
        """
        SELECT *
        FROM reports
        WHERE id = ?
        AND user_id = ?
        """,
        (
            report_id,
            session["user_id"]
        )
    ).fetchone()

    conn.close()

    if not report:
        abort(404)

    analysis = json.loads(
        report["analysis"]
    )

    return render_template(
        "report_analyze.html",
        report=report,
        analysis=analysis,
        extracted_text=report["report_text"]
    )


# ============================================================
# GET ALL SYMPTOMS
# ============================================================

def get_all_symptoms():

    symptoms = set()

    try:

        with open(
            "symptoms.csv",
            newline="",
            encoding="utf-8"
        ) as file:

            reader = csv.DictReader(
                file
            )

            for row in reader:

                symptom = row.get(
                    "symptom",
                    ""
                ).strip()

                if symptom:
                    symptoms.add(
                        symptom
                    )

    except FileNotFoundError:

        print(
            "ERROR: symptoms.csv was not found."
        )

    return sorted(
        symptoms
    )


# ============================================================
# SYMPTOM MATCHING
# ============================================================

def match_symptoms(user_text):

    normalized_input = normalize_text(
        user_text
    )

    input_words = set(
        normalized_input.split()
    )

    conditions = {}

    try:

        with open(
            "symptoms.csv",
            newline="",
            encoding="utf-8"
        ) as file:

            reader = csv.DictReader(
                file
            )

            for row in reader:

                condition = row.get(
                    "condition",
                    ""
                ).strip()

                symptom = row.get(
                    "symptom",
                    ""
                ).strip()

                if not condition or not symptom:
                    continue

                normalized_symptom = normalize_text(
                    symptom
                )

                if condition not in conditions:

                    conditions[condition] = {
                        "matched": set(),
                        "score": 0
                    }

                # --------------------------------------------
                # EXACT PHRASE MATCH
                # --------------------------------------------

                if (
                    normalized_symptom
                    in normalized_input
                ):

                    conditions[
                        condition
                    ]["matched"].add(
                        symptom
                    )

                    conditions[
                        condition
                    ]["score"] += 3

                    continue

                # --------------------------------------------
                # WORD MATCH
                # --------------------------------------------

                symptom_words = set(
                    normalized_symptom.split()
                )

                common_words = (
                    input_words
                    & symptom_words
                )

                if common_words:

                    conditions[
                        condition
                    ]["matched"].add(
                        symptom
                    )

                    conditions[
                        condition
                    ]["score"] += len(
                        common_words
                    )

                    continue

                # --------------------------------------------
                # SIMILAR WORD MATCH
                # --------------------------------------------

                word_similarity = 0

                for input_word in input_words:

                    for symptom_word in symptom_words:

                        ratio = difflib.SequenceMatcher(
                            None,
                            input_word,
                            symptom_word
                        ).ratio()

                        if ratio > word_similarity:
                            word_similarity = ratio

                if word_similarity >= 0.80:

                    conditions[
                        condition
                    ]["matched"].add(
                        symptom
                    )

                    conditions[
                        condition
                    ]["score"] += 1

    except FileNotFoundError:

        print(
            "ERROR: symptoms.csv was not found."
        )

    results = []

    for condition, data in conditions.items():

        if not data["matched"]:
            continue

        results.append(
            {
                "condition": condition,
                "matched": sorted(
                    data["matched"]
                ),
                "score": data["score"]
            }
        )

    results.sort(
        key=lambda item: item["score"],
        reverse=True
    )

    return results[:10]


# ============================================================
# DIAGNOSE / SYMPTOM PAGE
# ============================================================

@app.route(
    "/diagnose",
    methods=["GET", "POST"]
)
@login_required
def diagnose():

    if request.method == "POST":

        symptom_text = request.form.get(
            "symptoms",
            ""
        ).strip()

        duration = request.form.get(
            "duration",
            ""
        ).strip()

        severity = request.form.get(
            "severity",
            ""
        ).strip()

        age = request.form.get(
            "age",
            ""
        ).strip()

        sex = request.form.get(
            "sex",
            ""
        ).strip()

        medical_history = request.form.get(
            "medical_history",
            ""
        ).strip()

        medications = request.form.get(
            "medications",
            ""
        ).strip()

        allergies = request.form.get(
            "allergies",
            ""
        ).strip()

        additional_information = request.form.get(
            "additional_information",
            ""
        ).strip()

        if not symptom_text:

            flash(
                "Please enter at least one symptom.",
                "error"
            )

            return redirect(
                url_for("diagnose")
            )

        results = match_symptoms(
            symptom_text
        )

        possible_conditions = ", ".join(
            item["condition"]
            for item in results
        )

        conn = get_db()

        cursor = conn.execute(
            """
            INSERT INTO diagnoses
            (
                user_id,
                symptoms,
                duration,
                severity,
                age,
                sex,
                medical_history,
                medications,
                allergies,
                additional_information,
                possible_conditions
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session["user_id"],
                symptom_text,
                duration,
                severity,
                age,
                sex,
                medical_history,
                medications,
                allergies,
                additional_information,
                possible_conditions
            )
        )

        diagnosis_id = cursor.lastrowid

        conn.commit()
        conn.close()

        return redirect(
            url_for(
                "diagnosis_result",
                diagnosis_id=diagnosis_id
            )
        )

    return render_template(
        "diagnose.html",
        symptoms=get_all_symptoms(),
        results=[]
    )


# ============================================================
# DIAGNOSIS RESULT
# ============================================================

@app.route(
    "/diagnosis/<int:diagnosis_id>"
)
@login_required
def diagnosis_result(
    diagnosis_id
):

    conn = get_db()

    diagnosis = conn.execute(
        """
        SELECT *
        FROM diagnoses
        WHERE id = ?
        AND user_id = ?
        """,
        (
            diagnosis_id,
            session["user_id"]
        )
    ).fetchone()

    conn.close()

    if not diagnosis:
        abort(404)

    results = match_symptoms(
        diagnosis["symptoms"]
    )

    return render_template(
        "diagnose.html",
        diagnosis=diagnosis,
        symptoms=get_all_symptoms(),
        results=results
    )


# ============================================================
# PDF WRAPPED TEXT
# ============================================================

def write_wrapped_text(
    pdf,
    text,
    x,
    y,
    width=500,
    font_size=10,
    line_height=14
):

    pdf.setFont(
        "Helvetica",
        font_size
    )

    words = str(
        text or ""
    ).split()

    line = ""

    for word in words:

        test_line = (
            line + " " + word
        ).strip()

        if pdf.stringWidth(
            test_line,
            "Helvetica",
            font_size
        ) <= width:

            line = test_line

        else:

            pdf.drawString(
                x,
                y,
                line
            )

            y -= line_height

            line = word

            if y < 50:

                pdf.showPage()

                y = A4[1] - 50

                pdf.setFont(
                    "Helvetica",
                    font_size
                )

    if line:

        pdf.drawString(
            x,
            y,
            line
        )

        y -= line_height

    return y


# ============================================================
# CREATE REPORT ANALYSIS PDF
# ============================================================

def create_report_pdf(
    report,
    analysis
):

    filename = (
        f"report_analysis_"
        f"{report['id']}_"
        f"{uuid.uuid4().hex}.pdf"
    )

    path = os.path.join(
        GENERATED_FOLDER,
        filename
    )

    pdf = canvas.Canvas(
        path,
        pagesize=A4
    )

    width, height = A4

    y = height - 50

    # Title

    pdf.setFillColor(
        colors.HexColor("#087f8c")
    )

    pdf.setFont(
        "Helvetica-Bold",
        20
    )

    pdf.drawString(
        50,
        y,
        "Medical Report Analysis"
    )

    y -= 35

    pdf.setFillColor(
        colors.black
    )

    pdf.setFont(
        "Helvetica",
        10
    )

    pdf.drawString(
        50,
        y,
        f"Report: {report['report_name']}"
    )

    y -= 18

    pdf.drawString(
        50,
        y,
        f"Date: {report['created_at']}"
    )

    y -= 35

    # Laboratory values

    pdf.setFont(
        "Helvetica-Bold",
        13
    )

    pdf.drawString(
        50,
        y,
        "Extracted Laboratory Values"
    )

    y -= 25

    values = analysis.get(
        "values",
        []
    )

    if values:

        for item in values:

            y = write_wrapped_text(
                pdf,
                f"{item['name']}: {item['value']}",
                60,
                y
            )

    else:

        y = write_wrapped_text(
            pdf,
            "No supported laboratory values were confidently extracted.",
            60,
            y
        )

    y -= 15

    # Summary

    pdf.setFont(
        "Helvetica-Bold",
        13
    )

    pdf.drawString(
        50,
        y,
        "Analysis"
    )

    y -= 22

    y = write_wrapped_text(
        pdf,
        analysis.get(
            "summary",
            ""
        ),
        60,
        y
    )

    y -= 20

    # Extracted text

    pdf.setFont(
        "Helvetica-Bold",
        13
    )

    pdf.drawString(
        50,
        y,
        "Extracted Report Text"
    )

    y -= 22

    y = write_wrapped_text(
        pdf,
        report["report_text"] or "",
        60,
        y,
        width=480,
        font_size=9,
        line_height=12
    )

    if y < 100:

        pdf.showPage()

        y = height - 50

    y -= 20

    # Disclaimer

    pdf.setFillColor(
        colors.HexColor("#9a3412")
    )

    pdf.setFont(
        "Helvetica-Bold",
        10
    )

    pdf.drawString(
        50,
        y,
        "Important Medical Disclaimer"
    )

    y -= 18

    pdf.setFillColor(
        colors.black
    )

    disclaimer = (
        "This software provides educational decision-support "
        "information only. It does not provide a medical "
        "diagnosis and should not replace consultation with "
        "a qualified healthcare professional."
    )

    write_wrapped_text(
        pdf,
        disclaimer,
        50,
        y,
        width=500,
        font_size=9,
        line_height=12
    )

    pdf.save()

    return path


# ============================================================
# CREATE SYMPTOM MATCHING PDF
# ============================================================

def create_diagnosis_pdf(
    diagnosis,
    results
):

    filename = (
        f"symptom_analysis_"
        f"{diagnosis['id']}_"
        f"{uuid.uuid4().hex}.pdf"
    )

    path = os.path.join(
        GENERATED_FOLDER,
        filename
    )

    pdf = canvas.Canvas(
        path,
        pagesize=A4
    )

    width, height = A4

    y = height - 50

    # Title

    pdf.setFillColor(
        colors.HexColor("#087f8c")
    )

    pdf.setFont(
        "Helvetica-Bold",
        20
    )

    pdf.drawString(
        50,
        y,
        "Symptom Matching Report"
    )

    y -= 35

    pdf.setFillColor(
        colors.black
    )

    # Patient information

    information = [

        (
            "Symptoms",
            diagnosis["symptoms"]
        ),

        (
            "Duration",
            diagnosis["duration"]
        ),

        (
            "Severity",
            diagnosis["severity"]
        ),

        (
            "Age",
            diagnosis["age"]
        ),

        (
            "Sex",
            diagnosis["sex"]
        ),

        (
            "Medical History",
            diagnosis["medical_history"]
        ),

        (
            "Medications",
            diagnosis["medications"]
        ),

        (
            "Allergies",
            diagnosis["allergies"]
        ),

        (
            "Additional Information",
            diagnosis["additional_information"]
        )

    ]

    for label, value in information:

        if not value:
            value = "Not provided"

        pdf.setFont(
            "Helvetica-Bold",
            10
        )

        pdf.drawString(
            50,
            y,
            f"{label}:"
        )

        y -= 16

        y = write_wrapped_text(
            pdf,
            value,
            65,
            y,
            width=470,
            font_size=9,
            line_height=12
        )

        y -= 8

    # Results

    pdf.setFont(
        "Helvetica-Bold",
        13
    )

    pdf.drawString(
        50,
        y,
        "Possible Condition Matches"
    )

    y -= 25

    if not results:

        y = write_wrapped_text(
            pdf,
            "No strong symptom matches were found.",
            60,
            y
        )

    for result in results:

        pdf.setFont(
            "Helvetica-Bold",
            11
        )

        pdf.drawString(
            60,
            y,
            result["condition"]
        )

        y -= 17

        matched = ", ".join(
            result["matched"]
        )

        y = write_wrapped_text(
            pdf,
            f"Matching symptoms: {matched}",
            70,
            y,
            width=460,
            font_size=9,
            line_height=12
        )

        y -= 10

        if y < 100:

            pdf.showPage()

            y = height - 50

    # Disclaimer

    if y < 130:

        pdf.showPage()

        y = height - 50

    pdf.setFillColor(
        colors.HexColor("#9a3412")
    )

    pdf.setFont(
        "Helvetica-Bold",
        10
    )

    pdf.drawString(
        50,
        y,
        "Important Medical Disclaimer"
    )

    y -= 18

    pdf.setFillColor(
        colors.black
    )

    disclaimer = (
        "The conditions shown are possible matches based "
        "on symptom words and the software dataset. "
        "They are not confirmed diagnoses. Symptoms can "
        "have many causes. Consult a qualified healthcare "
        "professional for proper evaluation."
    )

    write_wrapped_text(
        pdf,
        disclaimer,
        50,
        y,
        width=500,
        font_size=9,
        line_height=12
    )

    pdf.save()

    return path


# ============================================================
# DOWNLOAD REPORT + ORIGINAL FILE
# ============================================================

@app.route(
    "/download/report/<int:report_id>"
)
@login_required
def download_report(report_id):

    conn = get_db()

    report = conn.execute(
        """
        SELECT *
        FROM reports
        WHERE id = ?
        AND user_id = ?
        """,
        (
            report_id,
            session["user_id"]
        )
    ).fetchone()

    conn.close()

    if not report:
        abort(404)

    analysis = json.loads(
        report["analysis"]
    )

    generated_pdf = create_report_pdf(
        report,
        analysis
    )

    zip_name = (
        f"medical_report_{report_id}.zip"
    )

    zip_path = os.path.join(
        GENERATED_FOLDER,
        zip_name
    )

    with zipfile.ZipFile(
        zip_path,
        "w",
        zipfile.ZIP_DEFLATED
    ) as archive:

        archive.write(
            generated_pdf,
            arcname="Medical_Report_Analysis.pdf"
        )

        original_path = os.path.join(
            UPLOAD_FOLDER,
            report["stored_filename"]
        )

        if os.path.exists(
            original_path
        ):

            archive.write(
                original_path,
                arcname=(
                    "Original_Report/"
                    + report["original_filename"]
                )
            )

    return send_file(
        zip_path,
        as_attachment=True,
        download_name=zip_name
    )


# ============================================================
# DOWNLOAD SYMPTOM REPORT
# ============================================================

@app.route(
    "/download/diagnosis/<int:diagnosis_id>"
)
@login_required
def download_diagnosis(
    diagnosis_id
):

    conn = get_db()

    diagnosis = conn.execute(
        """
        SELECT *
        FROM diagnoses
        WHERE id = ?
        AND user_id = ?
        """,
        (
            diagnosis_id,
            session["user_id"]
        )
    ).fetchone()

    conn.close()

    if not diagnosis:
        abort(404)

    results = match_symptoms(
        diagnosis["symptoms"]
    )

    pdf_path = create_diagnosis_pdf(
        diagnosis,
        results
    )

    return send_file(
        pdf_path,
        as_attachment=True,
        download_name=(
            "Symptom_Matching_Report.pdf"
        )
    )


# ============================================================
# MY REPORTS
# ============================================================

@app.route("/my-reports")
@login_required
def my_reports():

    conn = get_db()

    reports = conn.execute(
        """
        SELECT *
        FROM reports
        WHERE user_id = ?
        ORDER BY created_at DESC
        """,
        (
            session["user_id"],
        )
    ).fetchall()

    diagnoses = conn.execute(
        """
        SELECT *
        FROM diagnoses
        WHERE user_id = ?
        ORDER BY created_at DESC
        """,
        (
            session["user_id"],
        )
    ).fetchall()

    conn.close()

    return render_template(
        "my_reports.html",
        reports=reports,
        diagnoses=diagnoses
    )


# ============================================================
# RUN APPLICATION
# ============================================================

if __name__ == "__main__":

    app.run(
        debug=True
    )