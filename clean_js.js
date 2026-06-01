
    document.addEventListener("DOMContentLoaded", function() {
        const batchData = JSON.parse(document.getElementById('batch-data').textContent);
        const labChoices = JSON.parse(document.getElementById('lab-choices').textContent);
        
        // --- State ---
        let selectedBatches = new Set();
        let deselectedSubjects = {}; // batchId -> Set of subject names
        let parallelGroups = []; // {batch_id, batch_name, sub1, sub2}
        let batchPreferences = {}; // batch_id -> {gap}
        let labPreferences = {}; // lab_code -> {days, hours, gap}

        // Initialize UI Elements
        const mainBatchSelect = document.getElementById('mainBatchSelect');
        const subjectSelectionDiv = document.getElementById('subjectSelectionDiv');
        const subjectCheckboxContainer = document.getElementById('subjectCheckboxContainer');
        const parallelSubjectDiv = document.getElementById('parallelSubjectDiv');
        const pSub1Select = document.getElementById('pSub1Select');
        const pSub2Select = document.getElementById('pSub2Select');
        const currentParallelGroupList = document.getElementById('currentParallelGroupList');
        const batchGapDiv = document.getElementById('batchGapDiv');
        const prefBatchGap = document.getElementById('prefBatchGap');
        const addBatchToAllotmentBtn = document.getElementById('addBatchToAllotmentBtn');
        const configuredBatchesList = document.getElementById('configuredBatchesList');
        const emptyBatchState = document.getElementById('emptyBatchState');

        const prefLabSelect = document.getElementById('prefLabSelect');
        const prefList = document.getElementById('prefList');

        // Populate Lab Choices
        labChoices.forEach(([code, name]) => {
            let opt = document.createElement('option');
            opt.value = code;
            opt.textContent = name;
            prefLabSelect.appendChild(opt);
        });

        // Populate Main Batch Select
        batchData.forEach(batch => {
            let opt = document.createElement('option');
            opt.value = batch.id;
            opt.textContent = batch.name;
            mainBatchSelect.appendChild(opt);
        });

        // Handle Batch Selection
        mainBatchSelect.addEventListener('change', (e) => {
            const batchId = parseInt(e.target.value);
            if (!batchId) {
                subjectSelectionDiv.classList.add('hidden');
                parallelSubjectDiv.classList.add('hidden');
                batchGapDiv.classList.add('hidden');
                addBatchToAllotmentBtn.classList.add('hidden');
                return;
            }

            const batch = batchData.find(b => b.id === batchId);
            
            // Render Subjects
            subjectCheckboxContainer.innerHTML = '';
            batch.subjects.forEach(sub => {
                let sDiv = document.createElement('div');
                let isChecked = true;
                if (deselectedSubjects[batchId] && deselectedSubjects[batchId].has(sub)) {
                    isChecked = false;
                }
                sDiv.innerHTML = `
                    <label class="flex items-center space-x-2 text-sm cursor-pointer text-gray-700 dark:text-gray-300">
                        <input type="checkbox" class="batch-sub-cb form-checkbox h-4 w-4 text-blue-600" value="${sub}" ${isChecked ? 'checked' : ''}>
                        <span>${sub}</span>
                    </label>
                `;
                subjectCheckboxContainer.appendChild(sDiv);
            });
            subjectSelectionDiv.classList.remove('hidden');

            // Render Parallel Options
            pSub1Select.innerHTML = '<option value="">Subject 1</option>';
            pSub2Select.innerHTML = '<option value="">Subject 2</option>';
            batch.subjects.forEach(sub => {
                pSub1Select.insertAdjacentHTML('beforeend', `<option value="${sub}">${sub}</option>`);
                pSub2Select.insertAdjacentHTML('beforeend', `<option value="${sub}">${sub}</option>`);
            });
            renderParallelForCurrentBatch(batchId);
            parallelSubjectDiv.classList.remove('hidden');

            // Render Batch Gap
            prefBatchGap.value = batchPreferences[batchId] ? batchPreferences[batchId].gap : 1;
            batchGapDiv.classList.remove('hidden');

            addBatchToAllotmentBtn.classList.remove('hidden');
        });

        // Add Parallel Group
        document.getElementById('addParallelBtn').addEventListener('click', () => {
            const bId = parseInt(mainBatchSelect.value);
            const sub1 = pSub1Select.value;
            const sub2 = pSub2Select.value;
            if(bId && sub1 && sub2 && sub1 !== sub2) {
                const bName = mainBatchSelect.options[mainBatchSelect.selectedIndex].text;
                parallelGroups.push({batch_id: bId, batch_name: bName, sub1, sub2});
                renderParallelForCurrentBatch(bId);
            }
        });

        function renderParallelForCurrentBatch(batchId) {
            currentParallelGroupList.innerHTML = '';
            parallelGroups.forEach((pg, idx) => {
                if (pg.batch_id === batchId) {
                    let li = document.createElement('li');
                    li.className = "flex justify-between items-center bg-gray-200 dark:bg-gray-600 p-1 px-2 rounded";
                    li.innerHTML = `
                        <span>${pg.sub1} || ${pg.sub2}</span>
                        <button class="text-red-500 hover:text-red-700 font-bold ml-2" onclick="removeParallel(${idx}, ${batchId})">×</button>
                    `;
                    currentParallelGroupList.appendChild(li);
                }
            });
        }
        window.removeParallel = (idx, batchId) => { 
            parallelGroups.splice(idx, 1); 
            renderParallelForCurrentBatch(batchId); 
        };

        // Add Batch to Allotment Config
        addBatchToAllotmentBtn.addEventListener('click', () => {
            const batchId = parseInt(mainBatchSelect.value);
            if (!batchId) return;

            // Save Deselected Subjects
            if(!deselectedSubjects[batchId]) deselectedSubjects[batchId] = new Set();
            deselectedSubjects[batchId].clear();
            document.querySelectorAll('.batch-sub-cb').forEach(cb => {
                if (!cb.checked) {
                    deselectedSubjects[batchId].add(cb.value);
                }
            });

            // Save Batch Gap
            batchPreferences[batchId] = { gap: parseInt(prefBatchGap.value) };

            // Add to configured set
            selectedBatches.add(batchId);

            // Re-render configured list
            renderConfiguredBatches();

            // Reset form
            mainBatchSelect.value = "";
            mainBatchSelect.dispatchEvent(new Event('change'));
        });

        function renderConfiguredBatches() {
            if (selectedBatches.size === 0) {
                configuredBatchesList.innerHTML = '<li class="text-gray-400 italic" id="emptyBatchState">No batches configured yet.</li>';
                return;
            }
            configuredBatchesList.innerHTML = '';
            
            selectedBatches.forEach(bId => {
                const batch = batchData.find(b => b.id === bId);
                const gap = batchPreferences[bId] ? batchPreferences[bId].gap : 1;
                const pgs = parallelGroups.filter(p => p.batch_id === bId);
                
                let pgsText = pgs.length > 0 ? pgs.map(p => `${p.sub1}||${p.sub2}`).join(', ') : 'None';
                
                let li = document.createElement('li');
                li.className = "border rounded p-3 bg-gray-50 dark:bg-gray-700 dark:border-gray-600 flex justify-between items-start";
                li.innerHTML = `
                    <div>
                        <strong class="text-lg text-gray-800 dark:text-gray-200">${batch.name}</strong>
                        <div class="text-xs mt-1">Gap: ${gap} | Parallel: ${pgsText}</div>
                    </div>
                    <button class="text-red-500 hover:text-red-700 font-bold px-2 py-1 bg-red-100 dark:bg-red-900 rounded" onclick="removeConfiguredBatch(${bId})">Remove</button>
                `;
                configuredBatchesList.appendChild(li);
            });
        }

        window.removeConfiguredBatch = (bId) => {
            selectedBatches.delete(bId);
            delete deselectedSubjects[bId];
            delete batchPreferences[bId];
            parallelGroups = parallelGroups.filter(p => p.batch_id !== bId);
            
            // if this batch is currently selected in the dropdown, refresh it
            if (parseInt(mainBatchSelect.value) === bId) {
                mainBatchSelect.dispatchEvent(new Event('change'));
            }
            renderConfiguredBatches();
        };

        // Lab Pref Logic
        document.getElementById('addPrefBtn').addEventListener('click', () => {
            const lab = prefLabSelect.value;
            const days = document.getElementById('prefDays').value;
            const hours = document.getElementById('prefHours').value;
            const gap = document.getElementById('prefGap').value;
            
            if(lab === 'ALL') {
                labChoices.forEach(([code, _]) => {
                    labPreferences[code] = {days, hours, gap};
                });
            } else {
                labPreferences[lab] = {days, hours, gap};
            }
            renderPrefs();
        });

        function renderPrefs() {
            prefList.innerHTML = '';
            for(let lab in labPreferences) {
                let p = labPreferences[lab];
                let li = document.createElement('li');
                li.className = "flex justify-between items-center bg-gray-100 dark:bg-gray-700 p-2 rounded";
                li.innerHTML = `
                    <span><b>${lab}:</b> ${p.days} | ${p.hours} | Gap: ${p.gap}</span>
                    <button class="text-red-500 hover:text-red-700 font-bold" onclick="removePref('${lab}')">×</button>
                `;
                prefList.appendChild(li);
            }
        }
        window.removePref = (lab) => { delete labPreferences[lab]; renderPrefs(); };

        // Run
        document.getElementById('runBtn').addEventListener('click', () => {
            if(selectedBatches.size === 0) {
                alert("Please select at least one batch.");
                return;
            }

            const deselObj = {};
            for(let k in deselectedSubjects) {
                if(deselectedSubjects[k].size > 0) deselObj[k] = Array.from(deselectedSubjects[k]);
            }

            const prefArr = Object.keys(labPreferences).map(k => ({
                lab: k,
                days: labPreferences[k].days,
                hours: labPreferences[k].hours,
                gap: labPreferences[k].gap
            }));

            const bPrefArr = Object.keys(batchPreferences).map(k => ({
                batch_id: k,
                gap: batchPreferences[k].gap
            }));

            const payload = {
                selected_batches: Array.from(selectedBatches),
                deselected_subjects: deselObj,
                parallel_groups: parallelGroups,
                lab_preferences: prefArr,
                batch_preferences: bPrefArr
            };

            const btn = document.getElementById('runBtn');
            btn.textContent = "Processing...";
            btn.disabled = true;

            fetch("{% url 'api_run_auto_lab_allotment' %}", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "X-CSRFToken": "{{ csrf_token }}"
                },
                body: JSON.stringify(payload)
            }).then(res => res.json()).then(data => {
                if(data.status === 'success') {
                    alert("Allotment Successful!");
                    window.location.reload();
                } else {
                    alert("Error: " + data.error);
                }
            }).catch(err => {
                alert("Network error.");
                console.error(err);
            }).finally(() => {
                btn.textContent = "Run Auto Allotment";
                btn.disabled = false;
            });
        });
    });


