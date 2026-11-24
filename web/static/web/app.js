(function(){
  var side=document.querySelector('.side'),scrim=document.querySelector('.scrim'),burger=document.querySelector('.burger');
  function toggle(open){if(!side)return;side.classList.toggle('open',open);scrim.classList.toggle('open',open);burger&&burger.setAttribute('aria-expanded',open)}
  burger&&burger.addEventListener('click',function(){toggle(!side.classList.contains('open'))});
  scrim&&scrim.addEventListener('click',function(){toggle(false)});
  document.addEventListener('keydown',function(e){if(e.key==='Escape')toggle(false)});
  // prevent double submits
  document.querySelectorAll('form').forEach(function(f){f.addEventListener('submit',function(){
    var b=f.querySelector('button[type=submit].primary, button[type=submit]:not([formnovalidate])');
    if(b&&!b.dataset.busy){b.dataset.busy=1;setTimeout(function(){b.disabled=true;b.textContent=b.dataset.busyText||'Working…'},0)}})});
  // dialogs
  document.querySelectorAll('[data-dialog]').forEach(function(b){b.addEventListener('click',function(){var d=document.getElementById(b.dataset.dialog);d&&d.showModal()})});
  document.querySelectorAll('dialog [data-close]').forEach(function(b){b.addEventListener('click',function(){b.closest('dialog').close()})});
  // dismiss messages
  document.querySelectorAll('.msg button').forEach(function(b){b.addEventListener('click',function(){b.parentNode.remove()})});
  // move-in: fill rent/deposit from unit defaults
  var data=document.getElementById('unit-defaults');
  if(data){var d=JSON.parse(data.textContent),u=document.getElementById('id_unit'),r=document.getElementById('id_rent'),dep=document.getElementById('id_deposit');
    function fill(){var x=d[u.value];if(x){if(!r.value||r.dataset.auto)r.value=x.rent,r.dataset.auto=1;if(!dep.value||dep.dataset.auto)dep.value=x.deposit,dep.dataset.auto=1}}
    u&&u.addEventListener('change',fill);r&&r.addEventListener('input',function(){delete r.dataset.auto});dep&&dep.addEventListener('input',function(){delete dep.dataset.auto});u&&fill()}
  // payment form: show tenant for selected unit
  var info=document.getElementById('unit-info');
  if(info){var m=JSON.parse(document.getElementById('unit-info-data').textContent),sel=document.getElementById('id_unit');
    function show(){var x=m[sel.value];info.hidden=!x;if(x)info.innerHTML='<b>'+x.tenant+'</b><br>Owing: KES '+x.owing+(x.credit!=='0'?' · Credit: KES '+x.credit:'')}
    sel.addEventListener('change',show);show()}
})();
