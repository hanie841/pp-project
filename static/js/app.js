// PP Translation Services Portal - Dynamic Form Behavior

document.addEventListener('DOMContentLoaded', function() {

    // =============================================
    // Work Order Create: Service type toggle
    // =============================================
    const serviceTypeSelect = document.getElementById('id_service_type');
    if (serviceTypeSelect) {
        function toggleFields() {
            const isInterpretation = serviceTypeSelect.value === 'INTERPRETATION';
            document.querySelectorAll('.hours-group, .hours-field').forEach(el => {
                el.style.display = isInterpretation ? '' : 'none';
            });
            document.querySelectorAll('.pages-group, .pages-field').forEach(el => {
                el.style.display = isInterpretation ? 'none' : '';
            });
            // Clear hidden fields
            if (isInterpretation) {
                document.querySelectorAll('.pages-field').forEach(el => { el.value = ''; });
            } else {
                document.querySelectorAll('.hours-field').forEach(el => { el.value = ''; });
            }
        }
        serviceTypeSelect.addEventListener('change', toggleFields);
        toggleFields();
    }

    // =============================================
    // Work Order Create: Add/remove language rows
    // =============================================
    const addBtn = document.getElementById('addLanguage');
    if (addBtn) {
        addBtn.addEventListener('click', function() {
            const formContainer = document.getElementById('languageForms');
            const totalForms = document.getElementById('id_languages-TOTAL_FORMS');
            const currentCount = parseInt(totalForms.value);

            // Clone the first row
            const firstRow = formContainer.querySelector('.language-row');
            const newRow = firstRow.cloneNode(true);

            // Update form indices
            newRow.querySelectorAll('input, select, textarea').forEach(input => {
                if (input.name) {
                    input.name = input.name.replace(/-\d+-/, `-${currentCount}-`);
                }
                if (input.id) {
                    input.id = input.id.replace(/-\d+-/, `-${currentCount}-`);
                }
                if (input.type !== 'hidden') {
                    input.value = input.tagName === 'SELECT' ? '' : '';
                }
            });

            // Add remove button if not present
            const lastCol = newRow.querySelector('.col-md-1');
            if (lastCol && !lastCol.querySelector('.remove-language')) {
                const removeBtn = document.createElement('button');
                removeBtn.type = 'button';
                removeBtn.className = 'btn btn-sm btn-outline-danger remove-language';
                removeBtn.innerHTML = '<i class="bi bi-trash"></i>';
                lastCol.prepend(removeBtn);
            }

            // Reset estimated cost
            const costDisplay = newRow.querySelector('.estimated-cost');
            if (costDisplay) costDisplay.textContent = '0.00 درهم';

            formContainer.appendChild(newRow);
            totalForms.value = currentCount + 1;

            // Re-apply toggle
            if (serviceTypeSelect) toggleFields();
            bindRemoveButtons();
            bindCostCalculation();
        });
    }

    // =============================================
    // Remove language row
    // =============================================
    function bindRemoveButtons() {
        document.querySelectorAll('.remove-language').forEach(btn => {
            btn.removeEventListener('click', handleRemove);
            btn.addEventListener('click', handleRemove);
        });
    }

    function handleRemove() {
        const row = this.closest('.language-row');
        // Mark for deletion via Django formset
        const deleteCheckbox = row.querySelector('input[name$="-DELETE"]');
        if (deleteCheckbox) {
            deleteCheckbox.checked = true;
            row.style.display = 'none';
        } else {
            row.remove();
        }
        calculateTotal();
    }

    bindRemoveButtons();

    // =============================================
    // Cost calculation
    // =============================================
    function bindCostCalculation() {
        document.querySelectorAll('.language-row').forEach(row => {
            const langSelect = row.querySelector('.language-select');
            const hoursInput = row.querySelector('.hours-field');
            const pagesInput = row.querySelector('.pages-field');
            const transInput = row.querySelector('[name$="-num_translators"]');
            const costDisplay = row.querySelector('.estimated-cost');

            function calculateRowCost() {
                if (!langSelect || !costDisplay || typeof RATE_DATA === 'undefined') return;
                const langId = langSelect.value;
                if (!langId || !RATE_DATA[langId]) {
                    costDisplay.textContent = '0.00 درهم';
                    calculateTotal();
                    return;
                }
                const rates = RATE_DATA[langId];
                const translators = parseInt(transInput?.value) || 1;
                let amount = 0;

                if (hoursInput && hoursInput.style.display !== 'none' && hoursInput.value) {
                    amount = parseFloat(hoursInput.value) * parseFloat(rates.hourly) * translators;
                } else if (pagesInput && pagesInput.style.display !== 'none' && pagesInput.value) {
                    amount = parseFloat(pagesInput.value) * parseFloat(rates.page) * translators;
                }

                costDisplay.textContent = amount.toFixed(2) + ' درهم';
                calculateTotal();
            }

            [langSelect, hoursInput, pagesInput, transInput].forEach(el => {
                if (el) el.addEventListener('input', calculateRowCost);
                if (el) el.addEventListener('change', calculateRowCost);
            });

            // Initial calculation
            calculateRowCost();
        });
    }

    function calculateTotal() {
        let total = 0;
        document.querySelectorAll('.language-row').forEach(row => {
            if (row.style.display === 'none') return;
            const costDisplay = row.querySelector('.estimated-cost');
            if (costDisplay) {
                const val = parseFloat(costDisplay.textContent) || 0;
                total += val;
            }
        });
        const totalDisplay = document.getElementById('totalEstimate');
        if (totalDisplay) {
            totalDisplay.textContent = total.toFixed(2) + ' درهم';
        }
    }

    if (typeof RATE_DATA !== 'undefined') {
        bindCostCalculation();
    }
});
