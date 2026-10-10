(() => {
  "use strict";
  const toolSelect=document.getElementById("tool"), planSelect=document.getElementById("plan"), amountInput=document.querySelector('input[name="amount"]'), dataNode=document.getElementById("plans-data");
  if(!toolSelect||!planSelect||!amountInput||!dataNode)return;
  let plans; try{plans=JSON.parse(dataNode.textContent)}catch{return}
  const fmt=c=>`${Math.floor(c/100)},${String(c%100).padStart(2,"0")}`;
  function update(){const available=plans[toolSelect.value]||{},prev=planSelect.value;planSelect.replaceChildren();Object.entries(available).forEach(([plan,info])=>{const o=document.createElement("option");o.value=plan;o.textContent=plan;o.dataset.cents=String(info.amount_cents??info);planSelect.append(o)});if([...planSelect.options].some(o=>o.value===prev))planSelect.value=prev;const o=planSelect.selectedOptions[0];if(o)amountInput.value=fmt(Number(o.dataset.cents||0))}
  toolSelect.addEventListener("change",update);planSelect.addEventListener("change",()=>{const o=planSelect.selectedOptions[0];if(o)amountInput.value=fmt(Number(o.dataset.cents||0))});update();
})();