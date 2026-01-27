/**
 * Bulk Actions Module
 *
 * Provides Django-admin style multi-select and bulk action functionality
 * for list pages. Enable by including this script and adding the required
 * data attributes to your table.
 *
 * Required data attributes on table:
 *   data-bulk-actions - marks table as bulk-action enabled
 *   data-bulk-model   - model name for the action endpoint
 *
 * The table must have:
 *   - A checkbox column as the first column
 *   - Each row checkbox with data-row-id attribute
 *   - A select-all checkbox in the header with data-select-all attribute
 */

(function() {
    'use strict';

    function initBulkActions() {
        const table = document.querySelector('[data-bulk-actions]');
        if (!table) return;

        const model = table.dataset.bulkModel;
        const selectAllCheckbox = table.querySelector('[data-select-all]');
        const rowCheckboxes = table.querySelectorAll('[data-row-id]');
        const actionSelect = document.querySelector('[data-bulk-action-select]');
        const goButton = document.querySelector('[data-bulk-action-go]');
        const countDisplay = document.querySelector('[data-bulk-selected-count]');
        const totalCount = rowCheckboxes.length;

        if (!selectAllCheckbox || !actionSelect || !goButton) return;

        function updateSelectedCount() {
            const selectedCount = table.querySelectorAll('[data-row-id]:checked').length;
            if (countDisplay) {
                countDisplay.textContent = `${selectedCount} of ${totalCount} selected`;
            }
            // Enable/disable go button based on selection
            goButton.disabled = selectedCount === 0 || actionSelect.value === '';
        }

        function updateSelectAllState() {
            const checkedCount = table.querySelectorAll('[data-row-id]:checked').length;
            selectAllCheckbox.checked = checkedCount === totalCount && totalCount > 0;
            selectAllCheckbox.indeterminate = checkedCount > 0 && checkedCount < totalCount;
        }

        // Select all checkbox handler
        selectAllCheckbox.addEventListener('change', function() {
            rowCheckboxes.forEach(function(checkbox) {
                checkbox.checked = selectAllCheckbox.checked;
            });
            updateSelectedCount();
        });

        // Individual row checkbox handlers
        rowCheckboxes.forEach(function(checkbox) {
            checkbox.addEventListener('change', function() {
                updateSelectAllState();
                updateSelectedCount();
            });
        });

        // Action select handler
        actionSelect.addEventListener('change', function() {
            updateSelectedCount();
        });

        // Go button handler
        goButton.addEventListener('click', function(e) {
            e.preventDefault();

            const action = actionSelect.value;
            if (!action) {
                alert('Please select an action');
                return;
            }

            const selectedIds = [];
            table.querySelectorAll('[data-row-id]:checked').forEach(function(checkbox) {
                selectedIds.push(checkbox.dataset.rowId);
            });

            if (selectedIds.length === 0) {
                alert('Please select at least one item');
                return;
            }

            // Confirm action
            const actionLabel = actionSelect.options[actionSelect.selectedIndex].text;
            if (!confirm(`Are you sure you want to ${actionLabel.toLowerCase()} ${selectedIds.length} item(s)?`)) {
                return;
            }

            // Submit bulk action
            const form = document.createElement('form');
            form.method = 'POST';
            form.action = `/ui/${model}/bulk-action`;

            const actionInput = document.createElement('input');
            actionInput.type = 'hidden';
            actionInput.name = 'action';
            actionInput.value = action;
            form.appendChild(actionInput);

            // Include current URL for redirect back
            const returnInput = document.createElement('input');
            returnInput.type = 'hidden';
            returnInput.name = 'return_to';
            returnInput.value = window.location.pathname + window.location.search;
            form.appendChild(returnInput);

            selectedIds.forEach(function(id) {
                const idInput = document.createElement('input');
                idInput.type = 'hidden';
                idInput.name = 'ids';
                idInput.value = id;
                form.appendChild(idInput);
            });

            document.body.appendChild(form);
            form.submit();
        });

        // Initial state
        updateSelectedCount();
        updateSelectAllState();
    }

    // Initialize on DOM ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initBulkActions);
    } else {
        initBulkActions();
    }
})();
