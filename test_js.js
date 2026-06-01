const JSDOM = require('jsdom').JSDOM;
const dom = new JSDOM(`<!DOCTYPE html><html><body>
    <script id="batch-data" type="application/json">[{"id": 1, "name": "Batch A", "subjects": ["Math"]}]</script>
    <script id="lab-choices" type="application/json">[["L1", "Lab 1"]]</script>
    <select id="mainBatchSelect"></select>
    <div id="subjectSelectionDiv"></div>
    <div id="subjectCheckboxContainer"></div>
    <div id="parallelSubjectDiv"></div>
    <select id="pSub1Select"></select>
    <select id="pSub2Select"></select>
    <ul id="currentParallelGroupList"></ul>
    <div id="batchGapDiv"></div>
    <input id="prefBatchGap" value="1">
    <button id="addBatchToAllotmentBtn"></button>
    <ul id="configuredBatchesList"></ul>
    <div id="emptyBatchState"></div>
    <select id="prefLabSelect"></select>
    <ul id="prefList"></ul>
    <input id="prefDays" value="M">
    <input id="prefHours" value="1">
    <input id="prefGap" value="1">
    <button id="addPrefBtn"></button>
    <button id="addParallelBtn"></button>
    <button id="runBtn"></button>
</body></html>`);
const document = dom.window.document;
const window = dom.window;

// Now run the exact JS
try {
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

        console.log("Success! Select options count: " + mainBatchSelect.children.length);
} catch (e) {
    console.error(e);
}
