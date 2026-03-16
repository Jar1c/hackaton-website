// ==========================================
// 1. REGISTRATION (SIGN UP) LOGIC
// ==========================================
const signupForm = document.getElementById('signupForm');

if (signupForm) {
    signupForm.addEventListener('submit', async (e) => {
        e.preventDefault(); 

        const firstname = document.getElementById('firstname').value;
        const lastname = document.getElementById('lastname').value;
        const student_id = document.getElementById('student_id').value;
        const program = document.getElementById('program').value;
        const email = document.getElementById('email').value;
        const password = document.getElementById('password').value;

        try {
            const response = await fetch('/register', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ firstname, lastname, student_id, program, email, password })
            });

            const data = await response.json();
            
            if (data.status === "success") {
                alert("Account Created Successfully! You can now Sign In.");
                window.location.href = "/"; 
            } else {
                alert("Error: " + data.message);
            }
        } catch (error) {
            console.error("Error connecting to server:", error);
            alert("Something went wrong. Please try again.");
        }
    });
}

// ==========================================
// 2. LOGIN (SIGN IN) LOGIC
// ==========================================
const loginForm = document.getElementById('loginForm');

if (loginForm) {
    loginForm.addEventListener('submit', async (e) => {
        e.preventDefault(); 
        
        const email = document.getElementById('email').value;
        const password = document.getElementById('password').value;

        try {
            const response = await fetch('/login', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ email, password })
            });

            const data = await response.json();
            
            if (data.status === "success") {
                alert("Login Successful!");
                
                // 🔥 I-save ang info para hindi na kailangang mag-type ulit sa dashboard
                localStorage.setItem("student_id", data.student_id);
                localStorage.setItem("program", data.program);

                window.location.href = "/student_dashboard"; 
            } else {
                alert("Invalid Credentials. Please check your email or password.");
            }
        } catch (error) {
            console.error("Error connecting to server:", error);
            alert("Something went wrong. Please try again.");
        }
    });
}

// ==========================================
// 3. SUBMIT CONCERN LOGIC (STUDENT DASHBOARD)
// ==========================================
const concernForm = document.getElementById('concernForm');

if (concernForm) {
    // Helper function para i-load ang student data
    const loadInfo = () => {
        const sid = localStorage.getItem("student_id") || "";
        const prog = localStorage.getItem("program") || "";
        
        document.getElementById("student_id").value = sid;
        document.getElementById("program").value = prog;
        const displayId = document.getElementById("display-id");
        if (displayId) displayId.innerText = sid;
    };

    // Tawagin agad pag-load
    loadInfo();

    concernForm.addEventListener("submit", async function(e) {
        e.preventDefault();

        const msgEl = document.getElementById("form-msg");
        const fileInput = document.getElementById("fileInput");

        // Loading state
        msgEl.className = "loading";
        msgEl.style.display = "block";
        msgEl.innerText = "Submitting and auto-routing your concern…";

        // 🔥 FormData ang gamit para sa file upload support
        const formData = new FormData();
        formData.append("student_id", document.getElementById("student_id").value);
        formData.append("program", document.getElementById("program").value);
        formData.append("category", document.getElementById("category").value);
        formData.append("description", document.getElementById("description").value);
        formData.append("is_anonymous", document.getElementById("is_anonymous").value === "true");
        
        if (fileInput && fileInput.files.length > 0) {
            formData.append("attachment", fileInput.files[0]);
        }

        try {
            const res = await fetch("/submit_concern", {
                method: "POST",
                body: formData // Huwag maglagay ng JSON headers dito
            });
            const data = await res.json();

            if (data.status === "success") {
                msgEl.className = "success";
                // 🔥 Gamit ang data.tracking_id (A-001 format) galing Python
                msgEl.innerText = "✓ Concern submitted successfully! Tracking ID: " + data.tracking_id;
                
                // Linisin ang form at ibalik ang auto-fill data
                concernForm.reset();
                loadInfo();
                
                // I-reset ang file label UI text
                const fileLabel = document.getElementById('fileLabelText');
                if (fileLabel) {
                    fileLabel.innerHTML = `<strong>Click to upload</strong> or drag and drop a file here<br><span style="font-size:12px;">PDF, PNG, JPG up to 10MB</span>`;
                }
            } else {
                msgEl.className = "error";
                msgEl.innerText = "Error: " + data.message;
            }
        } catch (err) {
            console.error("Submit error:", err);
            msgEl.className = "error";
            msgEl.innerText = "Server connection failed. Please try again.";
        }
    });
}