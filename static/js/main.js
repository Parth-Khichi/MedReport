document.addEventListener("DOMContentLoaded", function () {

    const forms = document.querySelectorAll("form");

    forms.forEach(function (form) {

        form.addEventListener("submit", function () {

            const button = form.querySelector("button");

            if (button) {
                button.disabled = true;
                button.innerText = "Processing...";
            }

        });

    });

});