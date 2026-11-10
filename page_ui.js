// Read-only locators. All clicks are sent through CDP Input.dispatchMouseEvent.
(() => {
  const kind = KIND;
  function allRoots(root=document) {
    let out=[root];
    for (const e of root.querySelectorAll('*')) {
      if(e.shadowRoot) out.push(...allRoots(e.shadowRoot));
    }
    return out;
  }
  function parent(e) { return e.parentElement || e.getRootNode()?.host || null; }
  function visible(e) {
    if(!e || !e.isConnected) return false;
    const r=e.getBoundingClientRect();
    if(r.width<2 || r.height<2) return false;
    for(let n=e;n;n=parent(n)) {
      const s=getComputedStyle(n);
      if(s.display==='none'||s.visibility==='hidden'||Number(s.opacity)<.05||s.contentVisibility==='hidden')return false;
    }
    return true;
  }
  function text(e) { return (e?.innerText || e?.textContent || '').replace(/\s+/g,' ').trim(); }
  function label(e) { return (e.getAttribute('aria-label')||e.title||text(e)).trim(); }
  function deepHit(x,y) {
    let e=document.elementFromPoint(x,y);
    for(let i=0;i<12&&e?.shadowRoot;i++) {
      const next=e.shadowRoot.elementFromPoint(x,y);
      if(!next||next===e) break;
      e=next;
    }
    return e;
  }
  function contains(e,n) { for(let p=n;p;p=parent(p))if(p===e)return true; return false; }
  function candidate(e) {
    if(!visible(e)||e.disabled||e.getAttribute('aria-disabled')==='true')return null;
    const r=e.getBoundingClientRect(), x=r.x+r.width/2,y=r.y+r.height/2;
    if(x<0||y<0||x>=innerWidth||y>=innerHeight)return null;
    for(let n=e;n;n=parent(n))if(getComputedStyle(n).pointerEvents==='none')return null;
    const hit=deepHit(x,y);
    if(!contains(e,hit))return null;
    return {x,y,label:label(e).slice(0,60)};
  }
  function unique(elements) {
    const found=[...new Set(elements)].map(candidate).filter(Boolean);
    return found.length===1?found[0]:null;
  }
  const roots=allRoots();
  const elements=roots.flatMap(r=>[...r.querySelectorAll('*')]);
  const cards=[...document.querySelectorAll('.wb-slot--avatar-top-combo,.wb-slot--avatar-top')].filter(visible);
  function cardElements(){return cards.flatMap(c=>c.shadowRoot?[...c.shadowRoot.querySelectorAll('*')]:[...c.querySelectorAll('*')]);}
  const ce=cardElements();
  const claimed=ce.some(e=>visible(e)&&/^(今日已领取|今日已领|今日礼包已到账|今日已签到|今天已领取)$/.test(text(e)));
  if(kind==='status') {
    const b=[...document.querySelectorAll('.user-menu-billing-cell')].find(e=>text(e).includes('积分余额'));
    const match=b?text(b).match(/积分余额\s*([\d,]+)/):null;
    const claim=ce.filter(e=>e.matches('button,[role=button]')&&/^(立即领取|立刻领取|领取今日礼包|领取\s*\d+\s*积分)$/.test(label(e)));
    return {claimed,claim:unique(claim),profile:unique([...document.querySelectorAll('button.user-menu-trigger')]),
      menuOpen:!!document.querySelector('.wb-slot--menu-signin'),
      balance:match?Number(match[1].replace(/,/g,'')):null,
      loginRequired:!document.querySelector('button.user-menu-trigger')&&elements.some(e=>visible(e)&&e.children.length===0&&/^(登录|扫码登录|登录 WorkBuddy|微信扫码登录)$/.test(text(e))),
      startupError:[...document.querySelectorAll('.sk-overlay-content')].some(e=>visible(e)&&text(e).includes('启动失败'))};
  }
  if(kind==='profile')return unique([...document.querySelectorAll('button.user-menu-trigger')]);
  if(kind==='menu') {
    const slot=document.querySelector('.wb-slot--menu-signin');
    const r=slot?.shadowRoot;
    return r?unique([...r.querySelectorAll('.fuel-menu-entry')].filter(e=>text(e)==='Buddy加油站')):null;
  }
  if(kind==='claim')return claimed?null:unique(ce.filter(e=>e.matches('button,[role=button]')&&/^(立即领取|立刻领取|领取今日礼包|领取\s*\d+\s*积分)$/.test(label(e))));
  if(kind==='dismiss') {
    const promo=/活动|新品|新版本|体验官|限时|福利|抽奖|公告|重磅|发布|双\s*11|升级亮点/;
    const protectedContent=/扫码登录|验证码|输入密码|用户协议|隐私政策|确认支付|付款|购买成功|订阅确认|领取成功|今日已领取|未保存|保存更改|删除|放弃|确认退出|授权/;
    const close=/^(关闭|关闭弹窗|关闭广告|稍后再说|暂不体验|我知道了|知道了|下次再说|暂时不用|Close|Dismiss)$/i;
    const containers=elements.filter(e=>visible(e)&&(
      e.matches('dialog[open],[role=dialog],[aria-modal=true]')||
      /(?:modal|popup|announcement|marketing|center-overlay)/i.test(typeof e.className==='string'?e.className:'')
    ));
    for(const box of containers) {
      const content=text(box).slice(0,1800);
      if(!promo.test(content)||protectedContent.test(content))continue;
      const cls=typeof box.className==='string'?box.className:'';
      const identified=/marketing|promotion|advert|announcement-popup|wb-slot--.*(?:popup|modal|launch|center)/i.test(cls)
        || /(?:WorkBuddy|Hy4|混元|Buddy).{0,24}(?:发布|上新|新品|新版本|体验官|福利|抽奖|活动)/i.test(content);
      if(!identified)continue;
      const descendants=box.shadowRoot?[...box.shadowRoot.querySelectorAll('*')]:[...box.querySelectorAll('*')];
      if(descendants.some(e=>visible(e)&&e.matches('input:not([type=hidden]),textarea,[contenteditable=true]')))continue;
      // Marketing has a short title/copy. Editable task and document dialogs are excluded.
      if(content.length>1000)continue;
      const buttons=descendants.filter(e=>e.matches('button,[role=button],.close,.close-btn,.close-button,.popup-close,.modal-close,.x')&&close.test(label(e)));
      const result=unique(buttons);
      if(result)return {...result,category:'known_promotion'};
    }
    return null;
  }
  return null;
})()
