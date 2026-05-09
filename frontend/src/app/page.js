'use client';

import { useState } from 'react';
import Link from 'next/link';
import './landing.css';

function FaqItem({ num, q, a, defaultOpen }) {
  const [open, setOpen] = useState(defaultOpen || false);
  return (
    <div className={`faq-item${open ? ' open' : ''}`} onClick={() => setOpen(o => !o)}>
      <div className="num">{num}</div>
      <div className="q">{q}</div>
      <div className="a">{a}</div>
      <div className="toggle">{open ? '−' : '+'}</div>
    </div>
  );
}

export default function LandingPage() {
  return (
    <div className="landing" style={{ background: 'var(--cream)', color: 'var(--navy)', fontFamily: 'var(--sans)', fontSize: 15 }}>
      {/* Classification strip */}
      <div className="classification">
        <div className="cl-l">
          <span className="chip red">BEERANTIR &middot; INTERNAL</span>
          <span className="chip">PUBLIC RELEASE 0.4</span>
          <span className="sep">/</span>
          <span>BCN &middot; MOLLET DEL VALL&Egrave;S</span>
        </div>
        <div className="cl-r">
          <span>STATUS &middot; OPERATIONAL</span>
          <span className="sep">&middot;</span>
          <span>NODE BCN-OPS-02</span>
          <span className="sep">&middot;</span>
          <span>09 MAY 2026</span>
        </div>
      </div>

      {/* Nav */}
      <nav className="nav">
        <Link href="/" className="brand">
          <span className="mark">Beer<span className="ant">antir</span></span>
          <span className="v">v0.4 &middot; 2026</span>
        </Link>
        <div className="nav-links">
          <a href="#problem">Problem</a>
          <a href="#modules">Modules</a>
          <a href="#how">How it works</a>
          <a href="#numbers">Outcomes</a>
          <a href="#pricing">Pricing</a>
          <a href="#faq">FAQ</a>
        </div>
        <div className="nav-cta">
          <Link href="/console" className="btn ghost">Open console ↗</Link>
          <a href="#final" className="btn solid">Request a pilot</a>
        </div>
      </nav>

      {/* Hero */}
      <section className="hero" style={{ borderBottom: '1px solid var(--navy)', maxWidth: 'none' }}>
        <div className="hero-text" style={{ maxWidth: 1440 }}>
          <div className="hero-tag">
            <span className="pulse-dot"></span>
            Live &middot; 14 trucks routing through Barcelona right now
          </div>
          <h1>
            Operational intelligence<br />
            for the trucks that <em>still need to move</em>.
          </h1>
          <p className="lede">
            Beerantir is a dispatcher-grade decision layer for beverage logistics. Real routes, real loads, a co-pilot that explains the trade-offs &mdash; so route planners ship more pallets, with fewer empties, in tighter windows.
          </p>
          <div className="hero-cta">
            <a href="#final" className="btn red">Request a pilot &rarr;</a>
            <Link href="/console" className="btn">Open the dispatcher console ↗</Link>
          </div>
          <div className="hero-meta">
            <div className="stat">
              <div className="v">&minus;12<span className="u">% km / route</span></div>
              <div className="l">Avg distance saved</div>
            </div>
            <div className="stat">
              <div className="v">+18<span className="u">% pallet pack</span></div>
              <div className="l">Truck utilisation</div>
            </div>
            <div className="stat">
              <div className="v">94<span className="u">% on-window</span></div>
              <div className="l">Delivery reliability</div>
            </div>
          </div>
        </div>

        {/* Hero visual */}
        <div className="hero-visual">
          <div className="hv-head">
            <span>RTE-2026.05.09.A &middot; TRK-04</span>
            <span className="live">LIVE &middot; 10:48</span>
          </div>
          <div className="hv-canvas">
            <div className="hv-title">Route through <em>Barcelona</em> &middot; 7 stops</div>
            <div className="hv-route">
              <div className="hv-stop done"><span className="num">1</span><div><div className="name">Bar La Plata</div><div className="meta">S-01 &middot; BORN &middot; 1 PLT</div></div><span className="time">08:42 &#10003;</span></div>
              <div className="hv-stop done"><span className="num">2</span><div><div className="name">Bodega Joan</div><div className="meta">S-02 &middot; GR&Agrave;CIA &middot; 2 PLT</div></div><span className="time">09:38 &#10003;</span></div>
              <div className="hv-stop done"><span className="num">3</span><div><div className="name">Cervecería Catalana</div><div className="meta">S-03 &middot; EIXAMPLE &middot; 1 PLT</div></div><span className="time">10:24 &#10003;</span></div>
              <div className="hv-stop cur"><span className="num">4</span><div><div className="name">Cal Pep</div><div className="meta">S-04 &middot; BORN &middot; 1 PLT &middot; NOW</div></div><span className="time">11:06</span></div>
              <div className="hv-stop"><span className="num">5</span><div><div className="name">Quimet &amp; Quimet</div><div className="meta">S-05 &middot; POBLE SEC &middot; 1 PLT</div></div><span className="time">12:14</span></div>
              <div className="hv-stop"><span className="num">6</span><div><div className="name">Tickets Bar</div><div className="meta">S-06 &middot; SANT ANTONI &middot; 1 PLT</div></div><span className="time">13:08</span></div>
              <div className="hv-stop"><span className="num">7</span><div><div className="name">El Xampanyet <span className="prio"></span></div><div className="meta">S-07 &middot; BORN &middot; 1 PLT &middot; PRIORITY</div></div><span className="time">14:18</span></div>
            </div>
          </div>
          <div className="hv-foot">
            <div className="cell"><div className="l">Distance</div><div className="v">86<span className="u">km</span></div></div>
            <div className="cell"><div className="l">Drive time</div><div className="v">7:24<span className="u">h</span></div></div>
            <div className="cell"><div className="l">Score</div><div className="v">87<span className="u">/100</span></div></div>
            <div className="cell"><div className="l">Returnables</div><div className="v">71<span className="u">%</span></div></div>
          </div>
        </div>
      </section>

      {/* Logo strip */}
      <div className="logos">
        <span className="lab">Trusted by route planners at</span>
        <div className="row">
          <span className="logo">Damm Distribució</span>
          <span className="logo">Catalana Beverages</span>
          <span className="logo">Mollet Logistics</span>
          <span className="logo">Cervesa del Segre</span>
          <span className="logo">PortBev</span>
          <span className="logo">Vall del Foix</span>
        </div>
      </div>

      {/* Problem */}
      <section id="problem">
        <div className="problem">
          <div>
            <div className="section-eyebrow"><span className="num">01</span> Problem</div>
            <h2 className="section-title">
              Most route software is built for <em>e-commerce</em>. Beverage trucks live by different physics.
            </h2>
            <p className="section-sub">
              Pallets, not parcels. Tight delivery windows at bars and bodegas. Returnable kegs that have to come back. Cold chain. Driver routes that are also relationships. The default tools paper over all of it.
            </p>
          </div>
          <div className="problem-list">
            <div className="item">
              <div className="num">01</div>
              <div className="h">Black-box optimisation</div>
              <div className="b">Why this stop order? Why this truck? Planners can&rsquo;t explain the route to a driver &mdash; let alone override it.</div>
            </div>
            <div className="item">
              <div className="num">02</div>
              <div className="h">Loading is an afterthought</div>
              <div className="b">By SKU, by client, by axle weight &mdash; the right answer changes by run. Most tools pick one and ignore the rest.</div>
            </div>
            <div className="item">
              <div className="num">03</div>
              <div className="h">Returnables get lost</div>
              <div className="b">Empty kegs, crates, deposits. Reverse logistics doubles the moves and never makes it onto the screen.</div>
            </div>
            <div className="item">
              <div className="num">04</div>
              <div className="h">Replanning takes hours</div>
              <div className="b">Cancelled order at 11:00. Diagonal closes at 14:00. By the time the new plan exists, the truck has already paid the cost.</div>
            </div>
          </div>
        </div>
      </section>

      {/* Modules */}
      <section id="modules">
        <div className="section-eyebrow"><span className="num">02</span> Modules</div>
        <h2 className="section-title">A console, not a dashboard. Three panels that actually <em>talk to each other</em>.</h2>
        <p className="section-sub">Hover a pallet, the matching stop highlights on the map. Drop a stop, the load re-balances. Pull a slider, the metrics update live. Same surface as the dispatchers already use &mdash; minus the spreadsheet.</p>
        <div className="modules-grid">
          <div className="module">
            <div className="code">RTE &middot; MAP</div>
            <div className="h">The <em>Route</em> panel</div>
            <div className="b">Real Barcelona map, real OSRM driving routes. Stop windows, neighbourhood codes, priority flags. Completed legs solid, upcoming dashed.</div>
            <div className="feat">
              <div className="row"><span className="ck">&rarr;</span><span>OSRM driving routes, live</span></div>
              <div className="row"><span className="ck">&rarr;</span><span>Stop tooltips with ETA + window</span></div>
              <div className="row"><span className="ck">&rarr;</span><span>Hover sync to truck panel</span></div>
              <div className="row"><span className="ck">&rarr;</span><span>Coord chips &middot; WGS84 &middot; UTM 31T</span></div>
            </div>
          </div>
          <div className="module">
            <div className="code">TRK &middot; LOAD</div>
            <div className="h">The <em>Load</em> panel</div>
            <div className="b">Top-down view of the truck bed. Three loading modes &mdash; by reference, by client, hybrid &mdash; with a manifest table and a per-mode legend so the colours mean something.</div>
            <div className="feat">
              <div className="row"><span className="ck">&rarr;</span><span>Three loading strategies, FLIP-animated</span></div>
              <div className="row"><span className="ck">&rarr;</span><span>Pallet manifest with axle weights</span></div>
              <div className="row"><span className="ck">&rarr;</span><span>Returnables tracked per pallet</span></div>
              <div className="row"><span className="ck">&rarr;</span><span>Click pallet &rarr; client order detail</span></div>
            </div>
          </div>
          <div className="module">
            <div className="code">CLD &middot; COPILOT</div>
            <div className="h">The <em>Co-pilot</em></div>
            <div className="b">Claude, with full context on the run. Asks questions, explains trade-offs, fires scenarios. System log feed shows every decision the planner &mdash; and the agent &mdash; made.</div>
            <div className="feat">
              <div className="row"><span className="ck">&rarr;</span><span>Why-this-route explanations</span></div>
              <div className="row"><span className="ck">&rarr;</span><span>One-click traffic + cancellation replanning</span></div>
              <div className="row"><span className="ck">&rarr;</span><span>Audit trail of every override</span></div>
              <div className="row"><span className="ck">&rarr;</span><span>Slider-driven weight tuning</span></div>
            </div>
          </div>
        </div>
      </section>

      {/* How */}
      <section id="how">
        <div className="section-eyebrow"><span className="num">03</span> How it works</div>
        <h2 className="section-title">From depot manifest to driver phone, in <em>four steps</em>.</h2>
        <p className="section-sub">Beerantir doesn&rsquo;t replace your TMS. It sits on top &mdash; reading orders, returning routes, exposing the trade-offs. Two-week pilot, one truck, real data.</p>
        <div className="how-steps">
          <div className="step">
            <div className="num">01 &middot; INGEST</div>
            <div className="h">Read your orders</div>
            <div className="b">Connect to your TMS, ERP, or a CSV drop. Beerantir maps SKUs, clients, windows, and returnables into a unified run model.</div>
          </div>
          <div className="step">
            <div className="num">02 &middot; PLAN</div>
            <div className="h">Optimise the run</div>
            <div className="b">Route, load, and unload weights tunable per planner. The console makes every constraint visible &mdash; including the soft ones.</div>
          </div>
          <div className="step">
            <div className="num">03 &middot; DISPATCH</div>
            <div className="h">Hand it to the driver</div>
            <div className="b">Manifest goes to the in-cab tablet with stop order, ETA, returnables, and the planner&rsquo;s override notes &mdash; not just GPS coordinates.</div>
          </div>
          <div className="step">
            <div className="num">04 &middot; ADAPT</div>
            <div className="h">Replan as the day moves</div>
            <div className="b">Cancellations, traffic, late deliveries &mdash; the co-pilot proposes the change with the cost in km, minutes, and score. You approve.</div>
          </div>
        </div>
      </section>

      {/* Numbers */}
      <div className="numbers" id="numbers">
        <div className="numbers-inner">
          <div className="stat">
            <div className="v">&minus;12<span className="u">% km</span></div>
            <div className="l">Distance per route</div>
            <div className="delta">Across 4 BCN pilots &middot; 6 weeks</div>
          </div>
          <div className="stat">
            <div className="v">+18<span className="u">% pack</span></div>
            <div className="l">Pallet utilisation</div>
            <div className="delta">Hybrid loading vs. by-reference</div>
          </div>
          <div className="stat">
            <div className="v">94<span className="u">% in-window</span></div>
            <div className="l">On-time delivery</div>
            <div className="delta">From a baseline of 81%</div>
          </div>
          <div className="stat">
            <div className="v">7<span className="u">min</span></div>
            <div className="l">Faster per stop</div>
            <div className="delta">Avg pick + unload time</div>
          </div>
        </div>
      </div>

      {/* Testimonial */}
      <section>
        <div className="testimonial">
          <div>
            <div className="section-eyebrow"><span className="num">04</span> What planners say</div>
            <p className="quote">
              For the first time in fifteen years, I can <em>show</em> a driver why the route is the route. The co-pilot is the part I didn&rsquo;t know I&rsquo;d been missing.
            </p>
            <div className="quote-by">
              <div className="av">M</div>
              <div className="who">
                <b>Manel Puig</b>
                <span>Traffic Manager &middot; Damm Distribució &middot; Mollet del Vallès</span>
              </div>
            </div>
          </div>
          <div className="testi-card">
            <div className="stat">
              <div className="v">&minus;14%</div>
              <div className="l">km / route</div>
            </div>
            <div className="stat">
              <div className="v">+22%</div>
              <div className="l">pallet utilisation</div>
            </div>
            <div className="stat">
              <div className="v">2h</div>
              <div className="l">saved per planner / day</div>
            </div>
            <div className="stat">
              <div className="v">96%</div>
              <div className="l">on-window deliveries</div>
            </div>
            <div className="ctx">
              <span>PILOT &middot; DAMM &middot; 6 WEEKS &middot; 12 TRUCKS</span>
              <span>RTE-DAMM-2026.04</span>
            </div>
          </div>
        </div>
      </section>

      {/* Pricing */}
      <section id="pricing">
        <div className="section-eyebrow"><span className="num">05</span> Pricing</div>
        <h2 className="section-title">Priced per truck. <em>Not per click.</em></h2>
        <p className="section-sub">No per-API-call surprises, no minimum seats. The dispatcher console is one seat per planner; trucks are billed monthly.</p>
        <div className="pricing">
          <div className="tier">
            <div className="tag">PILOT</div>
            <div className="name">Garrofa</div>
            <div className="price"><span className="v">&euro;0</span><span className="u">/ first 6 weeks</span></div>
            <div className="desc">A single truck, your real routes, our team in the room. We ship insights weekly.</div>
            <ul className="features">
              <li><span className="ck">&rarr;</span><span>1 truck, 1 planner</span></li>
              <li><span className="ck">&rarr;</span><span>OSRM routing</span></li>
              <li><span className="ck">&rarr;</span><span>Co-pilot with audit trail</span></li>
              <li><span className="ck">&rarr;</span><span>CSV import / export</span></li>
              <li><span className="ck">&rarr;</span><span>Weekly review session</span></li>
            </ul>
            <div className="cta"><a href="#final" className="btn">Start a pilot</a></div>
          </div>
          <div className="tier featured">
            <div className="tag">FLEET</div>
            <div className="name">Estrella</div>
            <div className="price"><span className="v">&euro;189</span><span className="u">/ truck / month</span></div>
            <div className="desc">Production deployment for everyday operations. The whole console, every module, billed by truck.</div>
            <ul className="features">
              <li><span className="ck">&rarr;</span><span>Unlimited planner seats</span></li>
              <li><span className="ck">&rarr;</span><span>TMS &amp; ERP connectors</span></li>
              <li><span className="ck">&rarr;</span><span>In-cab driver app</span></li>
              <li><span className="ck">&rarr;</span><span>Real-time replanning</span></li>
              <li><span className="ck">&rarr;</span><span>SLA &middot; 99.5% uptime</span></li>
            </ul>
            <div className="cta"><a href="#final" className="btn solid">Talk to sales</a></div>
          </div>
          <div className="tier">
            <div className="tag">ENTERPRISE</div>
            <div className="name">Reserva</div>
            <div className="price"><span className="v">Custom</span></div>
            <div className="desc">Multi-depot operations, on-prem deployment, custom optimisation weights, dedicated forward-deployed engineer.</div>
            <ul className="features">
              <li><span className="ck">&rarr;</span><span>Multi-region routing</span></li>
              <li><span className="ck">&rarr;</span><span>On-prem / VPC deploy</span></li>
              <li><span className="ck">&rarr;</span><span>Custom optimisation models</span></li>
              <li><span className="ck">&rarr;</span><span>Forward-deployed engineer</span></li>
              <li><span className="ck">&rarr;</span><span>SOC 2 &middot; GDPR &middot; data residency</span></li>
            </ul>
            <div className="cta"><a href="#final" className="btn">Contact</a></div>
          </div>
        </div>
      </section>

      {/* FAQ */}
      <section id="faq">
        <div className="section-eyebrow"><span className="num">06</span> FAQ</div>
        <h2 className="section-title">Questions planners ask in the <em>first ten minutes</em>.</h2>
        <div className="faq-list">
          <FaqItem num="01" defaultOpen q="Does Beerantir replace our TMS?" a="No. Beerantir reads from your existing TMS and writes manifests back to it. We sit on top of the source-of-truth — orders, fleet, drivers — and add the optimisation, the explanation, and the in-cab handoff." />
          <FaqItem num="02" q="How long is a pilot?" a="Six weeks, one truck, your real routes. Week one is connection and shadow-mode. Week two onwards we run live, side-by-side with the planner." />
          <FaqItem num="03" q="What does the co-pilot actually do?" a="Three things: explains a plan in plain language, proposes changes when something breaks (cancellation, traffic, late delivery), and keeps an audit log of every decision. The planner stays in charge — every action requires their approval." />
          <FaqItem num="04" q="Where does our data live?" a="EU data residency by default — Frankfurt and Madrid regions. On-prem deployment available on the Reserva tier. SOC 2 Type II in progress; GDPR-compliant from day one." />
          <FaqItem num="05" q="Do you handle reverse logistics?" a="Yes — that's the part most tools miss. Returnable kegs, crates, and deposit containers are first-class objects in the load model. The driver sees them on the in-cab tablet; the planner sees the deposit liability." />
        </div>
      </section>

      {/* Final CTA */}
      <div className="final" id="final">
        <div className="final-inner">
          <div>
            <h2>The next route leaves at <em>06:00</em>.<br />Plan it with us.</h2>
            <p>Six-week pilot. One truck. Real routes. We bring the engineer; you bring the manifests. Most pilots see distance savings inside the first ten days.</p>
            <div className="final-cta">
              <a href="#" className="btn red">Request a pilot &rarr;</a>
              <Link href="/console" className="btn">Open the console ↗</Link>
            </div>
          </div>
          <div className="final-side">
            <div className="label">Pilot intake &mdash; May 2026</div>
            <div className="row"><span>Slots remaining</span><b>3 / 8</b></div>
            <div className="row"><span>Avg time-to-live</span><b>9 days</b></div>
            <div className="row"><span>Min commitment</span><b>1 truck &middot; 6 wk</b></div>
            <div className="row"><span>Cost during pilot</span><b>&euro;0</b></div>
            <div className="row"><span>Region</span><b>EU &middot; Iberia</b></div>
          </div>
        </div>
      </div>

      {/* Footer */}
      <footer>
        <div className="landing-footer">
          <div>
            <Link href="/" className="brand">
              <span className="mark">Beer<span className="ant">antir</span></span>
            </Link>
            <p className="blurb">Operational intelligence for beverage logistics. Built in Mollet del Vallès, deployed across the Iberian peninsula.</p>
          </div>
          <div>
            <div className="col-h">Product</div>
            <ul>
              <li><a href="#modules">Modules</a></li>
              <li><a href="#how">How it works</a></li>
              <li><a href="#pricing">Pricing</a></li>
              <li><Link href="/console">Console demo ↗</Link></li>
            </ul>
          </div>
          <div>
            <div className="col-h">Company</div>
            <ul>
              <li><a href="#">About</a></li>
              <li><a href="#">Careers</a></li>
              <li><a href="#">Press</a></li>
              <li><a href="#">Contact</a></li>
            </ul>
          </div>
          <div>
            <div className="col-h">Legal</div>
            <ul>
              <li><a href="#">Privacy</a></li>
              <li><a href="#">Terms</a></li>
              <li><a href="#">DPA</a></li>
              <li><a href="#">Status</a></li>
            </ul>
          </div>
        </div>
        <div className="footer-meta">
          <span>&copy; 2026 BEERANTIR S.L. &middot; MOLLET DEL VALL&Egrave;S &middot; BUILD 2026.05.09</span>
          <span>NODE BCN-OPS-02 &middot; CLEARANCE: PUBLIC</span>
        </div>
      </footer>
    </div>
  );
}
