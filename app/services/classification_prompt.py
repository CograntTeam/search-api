"""Verbatim company-classification prompt (ported from the n8n *AI Agent3* node).

The instruction text below is reproduced **as-is** from the n8n workflow. Only the
two template variables it interpolated are turned into substitution tokens that
:func:`build_classification_prompt` fills in:

* ``__COMPANY_DESCRIPTION__`` — the company's ``Company description`` field
* ``__TODAY__`` — today's date (ISO), for the age inference

It is a raw string so any ``\\n`` shown inside the example output schema stays
literal, exactly as the model saw it in n8n.
"""

from __future__ import annotations

COMPANY_CLASSIFICATION_INSTRUCTIONS = r"""ROLE: You are an expert in classifying companies for grant funding matching. You produce five classifications: eligibility basics (age and size), activity sectors (what the company actually does), verticality (which sectors the company impacts), beneficiary archetype (the functional identity funders implicitly target), and subject-matter expertise domains (deep scientific or specialised expertise the company demonstrates).

CONTEXT:
<description>
__COMPANY_DESCRIPTION__
</description>

═══════════════════════════════════════
CRITICAL PRINCIPLES (apply across all sections)
═══════════════════════════════════════

- Each axis answers a different question. Activity = the capability the team exercises. Verticality = the sectors the offering serves. Archetype = the functional identity grants target. Subject expertise = the deep scientific domain knowledge the company demonstrates. Do not conflate them.
- Classify only on real evidence in the description. A company having a website does not make it a software company. A company calling its product "AI-powered" does not make it AI engineering. The capability must be evidenced by what the company actually builds, sells, or operates.
- Be precise but inclusive at boundaries. A company that genuinely operates in a sector even partially should be classified there. Multi-classification is encouraged when capabilities overlap (e.g., a consultancy that productises some of its expertise into software belongs in both Business Advisory Services AND Software Product Development).
- Always select at least one activity sector and at least one beneficiary archetype. Verticality may be horizontal or null if signals are too thin. Subject-matter expertise may be empty — most companies have no claim to deep scientific subject expertise and that is the correct outcome.

═══════════════════════════════════════
SECTION 1 — ELIGIBILITY BASICS
═══════════════════════════════════════

`years_established` (integer | null):
- Years since the company was founded.
- Look for "founded in YYYY", "established YYYY", "since YYYY", "operating for X years", or registration-date statements.
- Calculate as current year minus founding year.
- Leave null only if the description has no founding signal whatsoever.

`company_size_class` (enum):
- "Micro" — 1–9 employees AND turnover ≤ €2M AND balance sheet ≤ €2M.
- "Small" — 10–49 employees AND turnover ≤ €10M AND balance sheet ≤ €10M.
- "Medium" — 50–249 employees AND turnover ≤ €50M AND balance sheet ≤ €43M.
- "Large" — ≥250 employees OR turnover > €50M.
- null — only if there are genuinely no signals to work with.

Determination logic — DO NOT bail out to null easily. Guess at least Small vs Medium when signals are mixed. Only return null if there is truly nothing to infer from.

1. Use stated headcount if present (most authoritative).
2. If a self-reported headcount range is given, take the upper bound.
3. If neither, INFER from a combination of these clues:
   - Number and complexity of products — single landing-page product points to Micro; multiple distinct products or platform components point to Small or larger.
   - Customer claims ("200+ customers", "trusted by 50 enterprises") → at least Small.
   - Number of offices — 3+ → Small or Medium.
   - Active hiring (open roles) — 5+ → at least Small.
   - Funding raised — €5M+ Series A typically Small or Medium; €20M+ usually Medium.
   - Sector reality — heavily regulated sectors (finance, pharma, energy infrastructure) require sizable teams to operate even at early stages; bias toward Small minimum.
   - Website depth — single-founder landing page → Micro; multi-product site with executive bios, careers, and case studies → Small or larger.
4. Cross-check headcount against turnover. EU SME rules require BOTH thresholds to be under the limit. If headcount says Small but turnover says Medium, the company is Medium.
5. Companies linked to a parent (consolidated parent figures) may lose SME status.

═══════════════════════════════════════
SECTION 2 — ACTIVITY SECTORS
═══════════════════════════════════════

═══════════════════════════════════════
ACTIVITY SECTOR PICKLIST — COMPANY SIDE
═══════════════════════════════════════

Same sector labels are used for activity-side classification (does this company perform this work?) and verticality classification (does this company serve this sector as customers?). Apply substantive evidence — reach past marketing copy to what the company actually ships, sells, or operates.

Verticality is inferred from observable website signals: named customer logos and case studies, industry-specific positioning and copy, stated mission focus, and concentration of named use cases. Without explicit data on revenue mix, treat verticality as a reasoned presumption from these signals — concentrate-meaningfully means the signals collectively read as a clear industry orientation, not as a single passing mention.

PHYSICAL PRODUCTION

"Industrial Production"
Identity: The economic activity of operating industrial-scale facilities that produce complex physical goods at volume, where the production capability — lines, robotics integration, process control, certification under industrial standards — is the value being delivered.
Activity-side test: A company is in this sector when it operates physical industrial production facilities as a primary commercial activity. If the company designs products but contracts production to external manufacturers without operating production capacity, this sector does not apply — the company's commercial substance is design, not operation.
Verticality-side test: A company can be presumed to have this verticality when website signals concentrate meaningfully on industrial producers — buyers operating their own industrial production — as the served customer base.
Boundary tests:
- vs Hardware & Embedded Systems Engineering: if the company designs products or writes software that runs on hardware, Hardware & Embedded. If the company operates production facilities producing those goods, this sector.
- vs Industrial & Enterprise Applications: if the company sells software to industrial buyers but does not operate production, Industrial & Enterprise Applications. If it operates production, this sector.
Not for: companies that own product designs and contract their manufacturing without operating physical production; equipment vendors selling tools to manufacturers; companies that use "industrial-grade" branding but operate at SMB or service scale.

"Textiles and Consumer Goods Production"
Identity: The economic activity of producing physical consumer items intended for direct end-user use — clothing, footwear, furniture, home goods, cosmetics, leather goods, or comparable categories — across the value chain from input materials to finished product.
Activity-side test: A company is in this sector when it produces or substantially transforms consumer-grade physical goods through textile, leather, cosmetic, or comparable production processes as a primary commercial activity, owning the production or owning the design and contracting production tightly under its specification.
Verticality-side test: A company can be presumed to have this verticality when website signals concentrate meaningfully on textile or consumer-goods producers as the served customer base.
Boundary tests:
- vs Industrial Production: if the goods are complex industrial assemblies, Industrial Production. If the goods are consumer items processed from textile, leather, or comparable lines, this sector.
- vs Retail and Wholesale Trade: if the company produces the goods, this sector. If the company only resells goods produced by others, Retail and Wholesale Trade.
- vs Content Production & Heritage: if the company sells design as a creative service rather than producing goods, Content Production & Heritage.
Not for: pure retailers, drop-shippers, brands without owned design or specification; companies branded "fashion" that operate only as marketing or e-commerce on others' production.

"Chemicals and Advanced Materials"
Identity: The economic activity of synthesising, formulating, or processing chemical substances and engineered materials — polymers, composites, alloys, advanced and functional materials, fine and bulk chemicals — at scales from laboratory to industrial plant.
Activity-side test: A company is in this sector when it performs chemical synthesis, formulation, or materials processing as a primary commercial activity, operating chemistry or materials-engineering operations directly rather than reselling chemicals others made.
Verticality-side test: A company can be presumed to have this verticality when website signals concentrate meaningfully on chemicals or materials producers — buyers who themselves perform chemistry or use it in production — as the served customer base.
Boundary tests:
- vs Pharmaceuticals and Life Sciences: if substances are produced for medical or biotech purposes under pharmaceutical regulatory frameworks, Pharmaceuticals. If the chemistry is general-industrial or materials-focused, this sector.
- vs Industrial Production: if the company operates an industrial facility producing complex assembled goods, Industrial Production. If the deliverable is chemistry or materials themselves, this sector.
Not for: distributors that resell chemicals without performing chemistry; companies that consume chemicals in their own products without producing them; companies branded "advanced materials" that only consult or specify materials others make.

"Utilities and Commodities"
Identity: The economic activity of producing, transmitting, distributing, or trading essential commodities and energy carriers — electricity, gas, heat, water, fuels, raw materials — through capital-intensive licensed infrastructure, including extraction operations.
Activity-side test: A company is in this sector when it operates licensed utility, commodity-production, or extraction infrastructure as a primary commercial activity, holding operating licenses for utility, energy, water, or extractive infrastructure and running that physical infrastructure as the source of its revenue.
Verticality-side test: A company can be presumed to have this verticality when website signals concentrate meaningfully on utility operators, commodity producers, or extractive industries as the served customer base.
Boundary tests:
- vs Industrial Production: if the company produces physical goods at a manufacturing plant, Industrial Production. If the company produces or distributes energy, fuels, water, or raw materials directly through licensed infrastructure, this sector.
- vs Network and Computing Infrastructure: utility infrastructure for energy and water is this sector; physical telecom and data-centre infrastructure is Network and Computing Infrastructure.
Not for: software vendors selling tools to utilities; energy consultancies; commodity traders without licensed infrastructure; companies branded "energy" or "clean tech" that supply equipment but don't operate utility infrastructure themselves.

"Agriculture and Food"
Identity: The economic activity of cultivating crops, raising livestock, operating aquaculture or fisheries, or processing biological inputs into food, beverages, or comparable agricultural products.
Activity-side test: A company is in this sector when it operates cultivation, animal-husbandry, aquaculture, fishery, or food-processing operations as a primary commercial activity, running the biological production or food-processing infrastructure that produces what it sells.
Verticality-side test: A company can be presumed to have this verticality when website signals concentrate meaningfully on primary producers, food processors, or food-and-beverage businesses as the served customer base.
Boundary tests:
- vs Pharmaceuticals and Life Sciences: if the biological work produces medicines, vaccines, or diagnostics under pharmaceutical regulatory frameworks, Pharmaceuticals. If the biological work produces food, feed, beverages, or agricultural commodities, this sector.
- vs Retail and Wholesale Trade: if the company produces or processes food, this sector. If it only distributes or sells food produced by others, Retail and Wholesale Trade.
Not for: agri-tech software companies; agricultural consultancies; food distributors and retailers; companies branded "farm-to-table" that resell others' production.

"Pharmaceuticals and Life Sciences"
Identity: The economic activity of discovering, developing, validating, or manufacturing medicines, biologics, vaccines, diagnostics, or biotechnology reagents under regulated scientific frameworks bound by pharmaceutical, biotech, or medical-device regulations.
Activity-side test: A company is in this sector when it performs regulated scientific work to produce or validate biological or chemical substances for medical, diagnostic, or biotechnology purposes as a primary commercial activity, running lab-based or production-based scientific procedures bound by pharmaceutical, biotech, or in-vitro-diagnostic regulatory frameworks.
Verticality-side test: A company can be presumed to have this verticality when website signals concentrate meaningfully on pharmaceutical, biotech, diagnostic, or medical-products companies as the served customer base.
Boundary tests:
- vs Chemicals and Advanced Materials: if substances are produced for medical or diagnostic intent under pharmaceutical or biotech regulatory frameworks, Pharmaceuticals. If the chemistry is general-industrial, Chemicals and Advanced Materials.
- vs Healthcare service-provider archetype: this sector is the producer side. Hospitals, clinics, and care-delivery organisations are tagged at the archetype level, not as an activity sector here.
Not for: digital health software companies; healthcare advisory firms; hospital operators; pharmaceutical marketing agencies; companies branded "biotech" that only consult or invest.

"Construction and Infrastructure"
Identity: The economic activity of designing, engineering, building, renovating, or maintaining buildings or civil infrastructure — including residential, commercial, transport, water, energy-asset, and public works — through on-site construction or specialised civil engineering.
Activity-side test: A company is in this sector when it performs on-site construction, civil engineering, or building/infrastructure asset delivery as a primary commercial activity, executing the construction, civil-engineering design, or maintenance of physical built assets directly with revenue derived from that work.
Verticality-side test: A company can be presumed to have this verticality when website signals concentrate meaningfully on construction firms, infrastructure operators, or property developers as the served customer base.
Boundary tests:
- vs Industrial Production: if the company produces complex physical goods at industrial volume, Industrial Production. If the company produces unique built assets at sites, this sector.
- vs Industrial & Enterprise Applications: if the deliverable is the physical built work, this sector. If the deliverable is software managing construction operations, Industrial & Enterprise Applications.
Not for: companies that sell tools, software, or materials to construction firms without performing construction themselves; construction-tech SaaS providers; companies branded "construction tech" with no on-site capability.

MOVEMENT & EXCHANGE

"Logistics and Trade"
Identity: The economic activity of physically transporting, storing, or managing the flow of goods or people across supply chains — freight, passenger transport, warehousing, terminal operations, customs, last-mile delivery — through operated transport and storage networks.
Activity-side test: A company is in this sector when it operates physical transport, warehousing, terminal, or last-mile distribution operations as a primary commercial activity, running the physical movement, storage, or distribution infrastructure that generates its revenue.
Verticality-side test: A company can be presumed to have this verticality when website signals concentrate meaningfully on logistics operators, transport companies, or supply-chain infrastructure firms as the served customer base.
Boundary tests:
- vs Industrial & Enterprise Applications: if the company operates physical movement and storage, this sector. If the company sells software at the system layer to logistics firms, Industrial & Enterprise Applications.
- vs Retail and Wholesale Trade: if the company transports and stores goods without taking ownership, this sector. If the company takes ownership of inventory to resell, Retail and Wholesale Trade.
Not for: logistics-software vendors; trade-finance platforms; supply-chain consultancies; companies branded "logistics tech" that don't operate physical transport or storage.

"Retail and Wholesale Trade"
Identity: The economic activity of distributing and selling goods — bulk to other businesses (wholesale) or directly to end customers (retail) — across physical or digital storefronts, where the company takes ownership of goods to resell them.
Activity-side test: A company is in this sector when it takes ownership of inventory and sells it to business or end-customer buyers as a primary commercial activity, operating purchase-and-resale of physical goods with revenue derived from selling goods it owns inventory in.
Verticality-side test: A company can be presumed to have this verticality when website signals concentrate meaningfully on retailers, wholesalers, or trade-distribution businesses as the served customer base.
Boundary tests:
- vs Logistics and Trade: if the company transports and stores goods without taking ownership, Logistics and Trade. If the company takes ownership of inventory to resell, this sector.
- vs Producer sectors (Textiles, Agriculture, Hardware & Embedded, etc.): if the company produces the goods being sold, the producer sector applies.
- vs Consumer & Small-Business Applications: a marketplace platform that doesn't take inventory ownership and only matches buyers and sellers is the application sector, not this one.
Not for: producers selling their own production output; marketplace platforms without inventory ownership; companies branded "e-commerce" that operate only as software platforms for others.

CULTURAL PRODUCTION

"Content Production & Heritage"
Identity: The economic activity of creating and commercialising original cultural, creative, and aesthetic-design output, or stewarding cultural heritage — including narrative, performing, and visual arts; architectural, fashion, industrial, and interior design as creative service; and museums, archives, libraries, and heritage sites.
Activity-side test: A company is in this sector when it produces original cultural, creative, or aesthetic output, sells design as a creative service, or operates heritage-stewardship institutions as a primary commercial activity, with revenue derived from those outputs.
Verticality-side test: A company can be presumed to have this verticality when website signals concentrate meaningfully on cultural producers, heritage institutions, design clients, or audiences as cultural participants.
Boundary tests:
- vs Communications & PR Services: if the design output is for marketing or communications purposes, Communications & PR. If the design output is a creative or aesthetic artefact intended as a cultural product, this sector.
- vs Education & Training Services: if the company delivers formal educational programmes, Education & Training. If the deliverable is cultural content that may have educational use, this sector.
Not for: cultural-tech software companies; cultural-marketing agencies; distributors of others' cultural content; companies branded "creative" that operate only as marketing or advertising agencies.

SOFTWARE & DIGITAL

"Consumer & Small-Business Applications"
Identity: The economic activity of designing and shipping software products primarily for individual users or small-to-medium businesses, sold through self-serve, low-touch, or product-led sales motions, where value sits in user-facing functionality and the deal size and onboarding complexity are small.
Activity-side test: A company is in this sector when it designs, ships, and operates software products targeted at individual users or small-to-medium businesses through self-serve or low-touch motions as a primary commercial activity, owning a software codebase that ships as a user-facing product with revenue from product subscriptions or licenses bought through self-serve or low-touch channels.
Verticality-side test: This is a capability sector. It rarely fires as verticality — companies in this sector serve any industry as customers. Can be presumed only when website signals indicate the company exclusively sells to other consumer-and-SMB software companies, which is a narrow case.
Boundary tests:
- vs Industrial & Enterprise Applications: if the GTM is enterprise sales with high-touch onboarding, large contract values, and integration with legacy systems, Industrial & Enterprise Applications. If the GTM is self-serve or low-touch SMB-grade, this sector.
- vs Software Infrastructure & Developer Tools: if the customer is a developer or engineering team building on top of the product, Software Infrastructure. If the customer is an end-user or small business using the product directly, this sector.
- vs AI & Data Systems Engineering: if the company's defensible technical work is engineering proprietary AI or data systems, AI & Data Systems. If the company integrates third-party AI services into a user-facing product, this sector regardless of "AI-powered" branding.
Not for: products that integrate third-party AI APIs into a SaaS but brand themselves "AI-powered" (those are still here, AI is feature-level); companies with enterprise sales motions; companies whose product is bought by engineering teams.

"Industrial & Enterprise Applications"
Identity: The economic activity of designing and shipping complex application or system-level software for industrial, enterprise, or institutional customers, sold through enterprise sales motions with large contract values and significant integration alongside the product.
Activity-side test: A company is in this sector when it designs and ships system-level or enterprise application software for industrial or institutional buyers, sold through enterprise sales as a primary commercial activity, with significant deployment, integration, and professional services attached.
Verticality-side test: This is a capability sector. It rarely fires as verticality. The end-markets the software serves (manufacturing, finance, healthcare, defence) are captured by their own industry sectors when website signals indicate the company's customer concentration there is meaningful.
Boundary tests:
- vs Hardware & Embedded Systems Engineering: if the company's software's purpose requires it to execute on or directly control specific physical hardware, Hardware & Embedded. If the software runs at the system or business layer above hardware (may ingest hardware data via standard interfaces but does not control hardware), this sector.
- vs Consumer & Small-Business Applications: if the GTM is enterprise sales with large contracts and integration, this sector. If the GTM is self-serve SMB or consumer, Consumer & Small-Business Applications.
- vs Software Infrastructure & Developer Tools: if the customer is an engineering team using the product as infrastructure for their own software, Software Infrastructure. If the customer is a business or institution using the product as a final application, this sector.
Not for: SMB SaaS branded "industrial-grade"; consumer apps that occasionally close enterprise deals; software whose primary value is controlling specific hardware (Hardware & Embedded); pure platforms sold to engineering teams.

"Hardware & Embedded Systems Engineering"
Identity: The economic activity of designing physical-digital products end-to-end OR engineering software whose purpose is to execute on or directly control specific physical hardware — covering firmware, real-time control, embedded systems, mechanical and PCB engineering, and the hardware-software boundary.
Activity-side test: A company is in this sector when it designs physical electromechanical or mechatronic products end-to-end OR engineers software whose purpose is to execute on or control specific hardware as a primary commercial activity. If removing the hardware makes the company's deliverable purposeless, this sector applies.
Verticality-side test: This is a capability sector. It rarely fires as verticality. End-markets the company serves (industrial production, automotive, healthcare devices) are captured by their own industry sectors.
Boundary tests:
- vs Industrial & Enterprise Applications: if the company's software's purpose requires it to execute on or directly control specific hardware, this sector. If the software runs at the system or business layer above hardware without controlling it, Industrial & Enterprise Applications.
- vs Software Infrastructure & Developer Tools: if the company builds general-purpose infrastructure software not tied to specific hardware, Software Infrastructure. If the work is hardware-specific firmware or embedded control, this sector.
- vs Industrial Production: if the company operates production capacity at industrial scale, Industrial Production. If the company designs the hardware product or its embedded software, this sector.
Not for: pure SaaS that connects to off-the-shelf hardware via standard APIs without controlling it; companies that resell or integrate third-party hardware without designing it; "smart product" branding for SaaS with no embedded engineering.

"Software Infrastructure & Developer Tools"
Identity: The economic activity of building software whose primary customer is another software company, engineering team, or technical buyer — bought to build, run, deploy, operate, or sit underneath other software. Covers the full stack from operating systems and database engines at the foundational layer, through container runtimes and distributed-systems primitives, to platforms, middleware, integration infrastructure, observability, and developer tooling.
Activity-side test: A company is in this sector when it builds software whose intended user operates it as infrastructure for software-engineering work, sold to developers, engineering teams, or software organisations as a primary commercial activity, with paying customers using the product to build, run, deploy, or operate their own software systems.
Verticality-side test: This is a capability sector. It rarely fires as verticality unless website signals indicate the company exclusively serves other software-infrastructure firms.
Boundary tests:
- vs Application sectors (Consumer & SMB, Industrial & Enterprise Applications): if the buyer uses the product as a final user-facing product, an application sector. If the buyer uses the product to build their own software, this sector.
- vs AI & Data Systems Engineering: if the company's defensible technical work is engineering an AI/ML or large-scale data system, AI & Data Systems. If the company builds general software infrastructure not specifically tied to AI/ML engineering, this sector. Both co-fire when the company builds AI/ML infrastructure used by other AI engineers.
- vs Cybersecurity Engineering: if removing the security functionality changes the product's identity, Cybersecurity. If security is incidental to a product whose primary identity is enabling engineering work, this sector.
Not for: products sold to end-users or businesses for direct work; products fundamentally built around security capability; generic IT services for clients without owning the platform product; companies branded "infrastructure" or "strategic technology" that operate at application layer.

"Cybersecurity Engineering"
Identity: The economic activity of developing security products, services, or specialised security capabilities — endpoint protection, threat detection and response, vulnerability management, identity and access as a security primitive, encryption, security operations, offensive-security work, threat intelligence — where the security capability is what is sold.
Activity-side test: A company is in this sector when it builds or operates capability whose primary function is to defend, detect, respond to, or assess cyber threats as a primary commercial activity. If removing the security capability collapses the product's identity, this sector applies.
Verticality-side test: This is a capability sector. It fires as verticality only when website signals indicate the company exclusively sells to other cybersecurity firms — narrow but real.
Boundary tests:
- vs Software Infrastructure & Developer Tools: if the company's product identity is fundamentally a security capability, this sector. If the product is general developer infrastructure that incidentally includes security, Software Infrastructure. The diagnostic: would the product still exist meaningfully if its security functionality were stripped out? If no, this sector.
- vs Strategy & Management Consulting: if the company builds security products or operates security services with proprietary tradecraft, this sector. If the company only advises on cybersecurity policy or compliance without building or operating capability, Strategy & Management Consulting.
Not for: generic IT firms with incidental security offerings; compliance-only consultants; companies that maintain security hygiene as part of generic IT operations; companies branded "cybersecurity" that resell others' security products without proprietary capability.

"AI & Data Systems Engineering"
Identity: The economic activity of engineering AI/ML systems and large-scale data systems where the system itself is the value being produced — training infrastructure, MLOps platforms, distributed inference, novel model architectures, proprietary data pipelines at substantial scale, vector and retrieval systems engineered as defensible work.
Activity-side test: A company is in this sector when it engineers AI/ML or large-scale data systems where the system itself is the defensible technical work as a primary commercial activity. If the AI or data system were removed from the company's stack and the company would still have a meaningful product, the AI is feature-level — the company belongs in the appropriate Application sector. If not, the system itself is the company.
Verticality-side test: This is a capability sector. It fires as verticality only when website signals indicate the company exclusively serves other AI-engineering firms as customers.
Boundary tests:
- vs Application sectors: if the company's product is a user-facing or business-facing application that uses AI to deliver its function, the application sector applies. If the AI or data system is the product itself as proprietary engineered work, this sector.
- vs Software Infrastructure & Developer Tools: if the company builds general software infrastructure not specifically tied to AI/ML engineering, Software Infrastructure. If the company builds AI/ML systems as proprietary engineering, this sector. Both may co-fire when the company builds AI/ML infrastructure used by other AI engineers.
Not for: products that integrate third-party AI services without proprietary system engineering; "AI-powered" branded SaaS with no engineering of the AI itself; data-analytics consultancies that produce reports rather than systems; companies whose ML work is occasional fine-tuning of pre-trained models rather than primary engineering.

"Network and Computing Infrastructure"
Identity: The economic activity of operating physical or large-scale infrastructure for digital connectivity and compute — telecom networks, fibre and mobile infrastructure, undersea cables, data centres, cloud regions, internet service provision — at the physical infrastructure layer.
Activity-side test: A company is in this sector when it operates physical telecommunications networks, data-centre infrastructure, or large-scale compute infrastructure under telecom or data-centre regulatory frameworks as a primary commercial activity, holding operating responsibility for licensed or large-scale physical digital infrastructure.
Verticality-side test: A company can be presumed to have this verticality when website signals concentrate meaningfully on telecom operators, data-centre operators, or digital-infrastructure providers as the served customer base.
Boundary tests:
- vs Software Infrastructure & Developer Tools: if the company operates physical telecom or data-centre infrastructure, this sector. If the company builds software running on top of digital infrastructure, Software Infrastructure & Developer Tools.
- vs Utilities and Commodities: utility infrastructure for energy, water, and fuels is Utilities. Telecom and data-centre infrastructure is this sector.
Not for: telecom-software vendors; cloud SaaS companies; digital-services firms that consume infrastructure; companies branded "cloud" that operate as application-level SaaS rather than running infrastructure themselves.

KNOWLEDGE & ADVISORY SERVICES

"Strategy & Management Consulting"
Identity: The economic activity of advising client organisations on strategy, operations, organisational structure, transformation, and people decisions — sold as a service of professional advice, analysis, and recommendations.
Activity-side test: A company is in this sector when it provides professional advisory or implementation services on business and organisational matters to clients as a primary commercial activity, selling advisory hours, analytical work, or implementation support on strategy, management, organisational, or people topics.
Verticality-side test: This is a service-capability sector. Rarely fires as verticality unless website signals indicate the company exclusively serves other consulting firms.
Boundary tests:
- vs Financial, Tax & Accounting Services: if the advisory is regulated financial, tax, accounting, or audit practice delivered through credentialed accounting/audit/tax credential, Financial. If the advisory is general business strategy, this sector.
- vs Legal Services: if the advisory is regulated legal practice, Legal. Strategy advisory that touches legal matters without being legal practice stays here.
- vs Public Policy & Governance Advisory: if the client is a public body or government and the work is policy advisory, Public Policy. If the client is a business and the work is commercial strategy, this sector.
Not for: companies that productise advisory expertise into software (those are application sectors); in-house strategy teams; freelancers without commercial consulting practice; companies branded "consulting" that primarily sell software products.

"Financial, Tax & Accounting Services"
Identity: The economic activity of providing regulated financial advisory, accounting, audit, and tax services to clients — accountancy, audit, tax practice, financial advisory, valuation, corporate-finance work — through credentialed practice subject to professional regulatory frameworks.
Activity-side test: A company is in this sector when it provides regulated financial, accounting, audit, or tax practice to clients through credentialed professionals as a primary commercial activity, with revenue from credentialed financial-services delivery (audit opinions, tax filings, financial advisory, valuations) bound by professional accounting, audit, or tax regulatory standards.
Verticality-side test: This is a service-capability sector. Rarely fires as verticality unless website signals indicate the company exclusively serves other financial-services firms.
Boundary tests:
- vs Strategy & Management Consulting: if the work is regulated financial, tax, or audit practice, this sector. If the work is general business strategy or management advisory, Strategy & Management Consulting.
- vs Legal Services: tax practice that requires legal qualification is Legal Services. Tax practice through accounting or tax-advisory credential is this sector.
Not for: financial-tech software builders; financial intermediaries that invest or lend (those are an archetype, not this sector); finance functions inside product companies; companies branded "fintech" or "advisory" that primarily build software.

"Legal Services"
Identity: The economic activity of providing regulated legal advice, representation, and documentation to clients — corporate, commercial, IP, regulatory, employment, dispute-resolution, public, and data-protection law — through credentialed legal practice.
Activity-side test: A company is in this sector when it provides regulated legal practice to clients through credentialed legal professionals as a primary commercial activity, with revenue from legal output (advice, contract drafting, IP filing, regulatory submissions, litigation) delivered under jurisdictional bar or law-society regulation.
Verticality-side test: This is a service-capability sector. Rarely fires as verticality unless website signals indicate the company exclusively serves other legal-services firms.
Boundary tests:
- vs Strategy & Management Consulting: legal advice and representation is this sector; non-legal strategy or management advice is Strategy & Management Consulting.
- vs Financial, Tax & Accounting Services: tax practice that requires legal qualification sits here; tax practice delivered by tax accountants sits there.
- vs Public Policy & Governance Advisory: if the work is delivering law as a service to clients, Legal. If the work is policy design and governance advisory for public bodies, Public Policy.
Not for: legal-tech software companies; paralegal-only operations without credentialed legal practice; in-house legal functions; companies branded "legal" that primarily build software.

"Public Policy & Governance Advisory"
Identity: The economic activity of advising public bodies, governments, multilateral institutions, or regulators on policy design, governance frameworks, regulatory architecture, and public-sector reform — sold as professional services to public-sector or multilateral clients.
Activity-side test: A company is in this sector when it provides professional advisory services to public-sector or multilateral clients on policy, governance, or regulatory matters as a primary commercial activity, with revenue from advisory or analytical engagements with governments, regulators, or multilateral institutions.
Verticality-side test: This is a service-capability sector. Rarely fires as verticality.
Boundary tests:
- vs Strategy & Management Consulting: if the client is a public body and the work is policy or governance advisory, this sector. If the client is a business and the work is commercial strategy, Strategy & Management Consulting.
- vs Public Administration: this sector is private advisors to the public sector. Public Administration is the public sector itself.
- vs Legal Services: if the work is delivering law as a regulated service, Legal. If the work is non-legal policy analysis and recommendations, this sector.
Not for: public bodies themselves; in-house government policy units; think tanks publishing research without advisory client engagements; companies branded "policy" that primarily produce content or run events.

"Communications & PR Services"
Identity: The economic activity of providing marketing, advertising, branding, public-relations, content marketing, and visual-design services to clients — output is campaigns, content, brand work, and the visual artefacts that communicate them, including graphic, UX/UI, brand-identity, and advertising design when sold as service work for communications purposes.
Activity-side test: A company is in this sector when it delivers communications, marketing, branding, dissemination, or communications-flavoured visual-design work as a service to clients as a primary commercial activity, with revenue from selling campaigns, communications artefacts, brand assets, or visual-design output produced for communications purposes.
Verticality-side test: This is a service-capability sector. Rarely fires as verticality unless website signals indicate the company exclusively serves other communications agencies.
Boundary tests:
- vs Content Production & Heritage: if the design output is for marketing or communications purposes, this sector. If the design output is a creative or aesthetic artefact intended as a cultural product, Content Production & Heritage.
- vs Education & Training Services: if the company delivers a formal educational programme with curriculum, Education & Training. If the company disseminates information through campaigns or content without programme structure, this sector.
Not for: companies that build their own marketing-tech or comms-tech products (application sectors); in-house marketing functions; companies branded "marketing" that primarily build software.

"Education & Training Services"
Identity: The economic activity of designing and delivering formal education or training programmes to learners — schools, vocational centres, executive education, professional certification, online course production, language schools, corporate training providers — where the offering is the programme itself.
Activity-side test: A company is in this sector when it designs and delivers structured educational or training programmes to learners as a primary commercial activity, with revenue from delivering formal education or training programmes — with curriculum, instruction, assessment, and learner-outcome accountability — to learner audiences.
Verticality-side test: A company can be presumed to have this verticality when website signals concentrate meaningfully on education or training providers — buyers operating in the formal education sector — as the served customer base.
Boundary tests:
- vs Communications & PR Services: if the company delivers formal educational programmes with curriculum and assessment, this sector. If the company disseminates information through reports, conferences, or platforms without programme structure, Communications & PR.
- vs Content Production & Heritage: if the company sells formal educational programmes, this sector. If the company produces cultural artefacts that may have educational use, Content Production & Heritage.
Not for: knowledge dissemination via reports or platforms (Communications & PR); edtech software builders (application sectors); in-house corporate training functions; companies branded "edtech" that primarily build software.

PUBLIC SECTOR

"Public Administration"
Identity: The economic activity of governmental institutions performing public-policy implementation, regulation, and public-service delivery — government bodies, ministries, municipal authorities, and regulatory agencies operating as the state apparatus.
Activity-side test: A company is in this sector when it is itself a public-sector institution performing governmental functions — implementing policy, regulating, delivering public services — operating as the state apparatus rather than as a commercial supplier.
Verticality-side test: A company can be presumed to have this verticality when website signals concentrate meaningfully on public-sector institutions — government bodies, ministries, municipalities, or regulators — as the served customer base.
Boundary tests:
- vs Public Policy & Governance Advisory: this sector is the public bodies themselves. Public Policy & Governance Advisory is private advisors serving public bodies.
- vs Defence & Security Operations: this sector is civilian public administration. Defence & Security Operations is the armed-forces and intelligence side.
- vs Public Safety & Civil Protection: this sector is policy implementation and regulation. Public Safety & Civil Protection is operational law-enforcement, emergency response, and civil protection.
Not for: private companies that serve government as clients; quasi-public commercial bodies; companies branded "civic" that operate as private startups.

"Defence & Security Operations"
Identity: The economic activity of armed forces, defence agencies, intelligence services, and specialised defence-tech contractors whose primary function is military operations, defence procurement, or military supply.
Activity-side test: A company is in this sector when it is part of the defence apparatus — armed forces, defence agency, intelligence service, or contractor whose primary commercial activity is supplying defence operations under defence-specific regulatory frameworks, with revenue concentrated in defence customers.
Verticality-side test: A company can be presumed to have this verticality when website signals concentrate meaningfully on defence agencies or military buyers as the served customer base.
Boundary tests:
- vs Public Administration: this sector is the defence and intelligence side of the public sector. Civilian public administration is Public Administration.
- vs Public Safety & Civil Protection: this sector is military and defence. Civilian law-enforcement, emergency response, and civil protection is Public Safety & Civil Protection.
- vs Engineering sectors (Hardware & Embedded, AI & Data Systems, Cybersecurity): defence-tech engineering capabilities are captured in their respective engineering sectors. This sector is for the defence operators and defence-primary contractors themselves.
Not for: dual-use technology companies whose primary market is civilian; general-purpose tech companies that occasionally sell to defence; companies branded "defence-tech" without actual defence-customer concentration.

"Public Safety & Civil Protection"
Identity: The economic activity of institutions delivering public order, justice, emergency response, and civil protection — police, courts, prosecution, fire and rescue, emergency services, border control, civil protection agencies.
Activity-side test: A company is in this sector when it is a public-safety or civil-protection institution delivering law-enforcement, judicial, emergency-response, or civil-protection mandates as its primary public-sector function.
Verticality-side test: A company can be presumed to have this verticality when website signals concentrate meaningfully on public-safety or civil-protection institutions as the served customer base.
Boundary tests:
- vs Public Administration: this sector is the operational law-enforcement, justice, and emergency side of the public sector. Civilian policy implementation and regulation is Public Administration.
- vs Defence & Security Operations: this sector is civilian public-safety. Military and intelligence is Defence & Security Operations.
Not for: private security companies (their operational sector or service sector applies); public-safety software builders (application sectors); advisory firms that consult on public-safety topics (Public Policy & Governance Advisory).

═══════════════════════════════════════
SECTION 3 — VERTICALITY
═══════════════════════════════════════

Verticality describes which industry sectors the company's offering impacts or serves — its customer concentration. This is independent of what the company does internally; a "Software Product Development" company can be vertical (HealthTech, AgriFood) or horizontal (HR tools, generic productivity).

Two possible classifications:

A) is_horizontal = true — the offering is genuinely cross-sector with no concentration in any single industry. Use for general productivity tools, generic business services, broad consumer products. This is a positive classification (actual evidence of horizontality), not a fallback for "we don't know." When this is selected, vertical_sectors_impacted = [].

B) is_horizontal = false — the offering concentrates in one or more industry sectors. List those sectors using the activity-sector picklist above (e.g. ["Pharmaceuticals and Life Sciences"], ["Construction and Infrastructure", "Utilities and Commodities"]).

Concentration rule: include a vertical only if at least 30% of customers, named use cases, case studies, or evidenced revenue points to it. Below that threshold, do not list it.

If the description provides too little evidence to determine, leave is_horizontal = null and vertical_sectors_impacted = [].

═══════════════════════════════════════
SECTION 4 — BENEFICIARY ARCHETYPE
═══════════════════════════════════════

A beneficiary archetype is the functional identity that funders implicitly target with their grants. Even when a grant says "any legal entity is eligible," the supported activities, KPIs, evaluation criteria, and budget structure betray which kind of organisation it really wants. A company can be tagged with one or two archetypes; most have a single primary tag. Two tags are appropriate when the company has a genuine hybrid identity (e.g., a research-active SME spin-off is "R&D-performing / deep-tech SME").

Archetypes (use these labels exactly):

"Generic SME"
Established small or medium business (1–249 employees), running steady operations with light or no innovation focus.

"Innovative SME"
Established SME introducing market-novel products or services, with light-to-medium R&D activity. The novelty is meaningful but not deep-tech.

"R&D-performing / deep-tech SME"
Established SME with systematic in-house R&D, dedicated technical staff (often PhDs), IP-heavy work, or research-intensive technology development.

"Industrial Corporate"
250+ employees, capital-deployer, often a corporate group. Targets: Innovation Fund, large-corporate consortium roles, industrial decarbonisation, mid-cap programmes.

"Research Organisation"
Academic or applied research organisation whose primary function is producing scientific knowledge. Targets: ERC, Horizon RIA, basic research grants, Marie Curie institutional roles.

"Individual researcher"
A natural person applying for personal-fellowship grants. The grantee is the individual scientist, not an organisation. Targets: Marie Curie individual fellowships, ERC starting/consolidator/advanced grants, national fellowship schemes.

"Public authority"
Government bodies, ministries, agencies, municipalities, regional authorities. The applicant is a public-sector institution. Targets: cohesion funds, public-administration capacity programmes, Interreg, public-procurement-of-innovation schemes.

"Healthcare service provider"
Hospitals, clinics, care homes, public-health bodies. The institution delivers health services as its primary function. Targets: EU4Health, hospital modernisation, healthcare innovation calls.

"Educational provider"
Schools, vocational centres, training organisations whose primary function is delivering education to learners. Distinct from research-led universities (those are "Research organisation").

"NGO"
Non-profit advocacy, community groups, charities, foundations. Mission-driven non-profit organisations. Targets: Citizens, Equality, Rights and Values programme, LIFE NGO grants, regional NGO support.

"Primary-sector producer"
Farms, fisheries, aquaculture, forestry operations. Owns the cultivation, harvesting, or extraction operation. Targets: CAP, EMFAF, LEADER, national agriculture/forestry/fishery support.

"Cultural institution"
Museums, libraries, theatres, archives, heritage sites, cultural centres whose primary function is cultural stewardship or production. Targets: Creative Europe, national culture funds, heritage restoration grants.

"Network organisation"
Collective representation organisations — industry associations, professional bodies, business clusters, umbrella networks of NGOs or SMEs, sector federations, technology platforms, chambers of commerce. The applicant represents or coordinates a group of other entities, not itself producing goods or services for end customers. Targets: cluster grants, COSME successors, sector-coordination programmes, networking calls, umbrella-body capacity grants.

"Financial intermediary"
Banks, VC funds, equity providers, investment vehicles applying for grants that support their OWN activity (not their investee companies). Niche but real for blended-finance instruments. Targets: LIFE financial instruments, EIC Fund, blended-finance pilots.

═══════════════════════════════════════
SECTION 5 — SUBJECT-MATTER EXPERTISE DOMAINS
═══════════════════════════════════════

═══════════════════════════════════════
SECTION 6 — SUBJECT-MATTER EXPERTISE DOMAINS
═══════════════════════════════════════
This section captures the company's expertise in specific scientific or specialised subject-matter domains. Most grants do NOT require subject-matter expertise — they fund operational projects without requiring the applicant to know the science of the subject. But a minority of grants (deep-research and conservation calls) explicitly require the applicant to know the science. For those grants, only companies with credible domain expertise can be matched.
INFER FROM WHAT THE WEBSITE ACTUALLY SHOWS. The input is a website scrape and possibly legal-registry data — companies rarely list PhDs, publications, or grant track records on their public site. Waiting for that level of evidence would miss most legitimately expert companies. Tag a domain when the website signals collectively support the claim that the company itself could credibly bid for a research-grade call in this field. The signals that count:

Technical depth and specificity of described methodology, beyond marketing labels — e.g., specific architectures, methods, or scientific approaches described in their own terms rather than only as outcomes.
Breadth and coherence of capabilities only plausible with deep technical staff. A coherent stack of advanced methods in one domain implies expert team composition even when staff are not named.
Sector-specific regulated activity or certifications (CE/FDA filings, GMP manufacturing, ISO standards specific to a scientific domain).
Named research projects, prior grants, EU programme participation, named labs, named collaborators, or named proprietary methods.
Educational or training output in the domain (bootcamps, structured training programmes, published courses) — teaching the field at a credible level signals working mastery of it.
Domain-correct technical vocabulary used in context, not as marketing decoration.

WHAT STILL DOES NOT QUALIFY, EVEN WITH GENEROUS INFERENCE:

"AI-powered" or "data-driven" branding without described methodology.
Verticality signals masquerading as expertise — "we serve hospitals" is verticality, not biomedical science expertise. "We advise energy clients" is consulting on energy, not energy technology research.
Consultancies that advise on a domain without performing the work themselves.
Resellers, integrators, or distributors of others' science.
Generic capability lists with no technical specificity.
Application of off-the-shelf methods (third-party APIs, occasional fine-tuning of pre-trained models) without evidence of method development or research-grade work. Application of existing methods belongs in the relevant activity sector, NOT here.

The bar to clear is whether the company, as an applicant, could plausibly write a competitive proposal for a domain-specialist research call. If the work the website describes is operational deployment of methods invented elsewhere, the expertise tag does not apply — the activity sector classification already captures it.

Domains (use these labels exactly):

"Biomedicine & health sciences"
Clinical research, drug development, specific diseases, medical-device science, public-health epidemiology, biotech R&D. Evidence: clinical trials run, peer-reviewed medical publications, PhDs in medicine/biology/pharmacology, medical-device CE/FDA filings.

"Environment, ecosystems & biodiversity"
Terrestrial ecology, conservation biology, pollution science, soil science, habitat restoration. Evidence: ecology publications, field-research track record, biodiversity-monitoring projects, ecology PhDs on staff.

"Marine, ocean & freshwater sciences"
Oceanography, marine biology, hydrology, fisheries science, marine pollution research. Evidence: oceanographic studies, marine-biology publications, named research vessels or stations, marine-science PhDs.

"Climate & atmospheric science"
Climate modelling, atmospheric chemistry, meteorology, paleoclimatology. NOT for clean-tech deployment without research underpinning. Evidence: climate-model publications, atmospheric-science staff, research-grant track record.

"Earth observation, space & planetary science"
Geology, remote sensing, space missions, planetary science, satellite-instrument science. Evidence: space-mission contracts, satellite-instrument development, ESA/NASA-grade publications, geology research.

"Energy technology research"
Deep energy R&D — fusion, hydrogen technology research, advanced battery chemistry, grid-systems science. NOT for solar deployment, energy-management software, or general efficiency consulting. Evidence: physics/chemistry PhDs, published energy-tech research, lab-scale prototyping with reproducible results.

"Advanced materials & chemistry research"
Novel materials science, specific synthetic chemistry, nanomaterials, polymer science, catalysis research. Evidence: materials-science publications, chemistry PhDs, characterisation lab capability, materials patents.

"Quantum, photonics & advanced computing physics"
Quantum computing, quantum communications, photonics, sub-microelectronics physics, novel hardware-physics research. Evidence: physics PhDs, peer-reviewed quantum/photonics publications, named lab capability, quantum-flagship participation.

"Forestry, agronomy & food science research"
Plant science, crop research, livestock science, food chemistry, agronomic methodology, forest-system research. Evidence: agronomy publications, plant-science PhDs, named field-trial programmes, agricultural-research grants.

"Cultural heritage & conservation science"
Restoration science, archaeology methodology, conservation chemistry, paleography, heritage-material analysis. Evidence: published conservation work, named restoration projects, archaeology PhDs, museum-grade scientific staff.

"Defence & dual-use technology research"
Military-grade engineering research, dual-use scientific domains (sensors, autonomy, advanced materials for defence applications). Evidence: EDF / EDIDP track record, defence-research publications, security-cleared research staff, dual-use IP.

"AI/ML foundational research"
Algorithmic research, model-architecture innovation, foundational machine-learning methodology, specific theoretical AI work. NOT for application of existing models — that's covered by activity sectors. Evidence: NeurIPS/ICML/ICLR publications, ML PhDs with research output, novel model architectures shipped, peer-reviewed ML research.

`specialisations` (free-text, optional): if the company has named Level-2 specialisations within a domain (e.g., "marine biodiversity monitoring", "oncology drug development", "perovskite solar cells", "neutron physics"), list them as free-text strings. Empty list when no named specialisations.

═══════════════════════════════════════
TASK
═══════════════════════════════════════

1. Read the company description carefully. Identify what the company actually builds, sells, operates, or delivers; who its customers are; how long it has been operating; and what scale it operates at.

2. Determine eligibility basics: years_established (knowing today date is __TODAY__) and company_size_class. Apply inference rules generously; only return null when there is genuinely no signal.

3. Assign one or more activity sectors based strictly on what the company DOES. Apply the exclusion criteria — especially for Software, AI, Cybersecurity, and the advisory categories.

4. Assign verticality: either is_horizontal = true (with empty vertical list) or is_horizontal = false with the list of sectors the company concentrates in. is_horizontal = null only if signals are genuinely too thin.

5. Assign one (or at most two) beneficiary archetypes that capture the company's functional identity from a funder's perspective.

6. Assign subject-matter expertise domains ONLY if the company demonstrably has deep expertise. Most companies should have an empty list. Apply the conservative bar — soft signals do not qualify.

7. For each section, provide a brief reasoning (one or two sentences) tying the classification to specific evidence from the description.

ANSWER FORMAT: Return ONE JSON object only. Sector, archetype, and domain names must be written exactly as listed above. No other text, no markdown fences.

{
  "eligibility_basics": {
    "date_of_establishment": date
    "years_established": null,
    "company_size_class": "",
    "reasoning": ""
  },
  "activity_sectors": {
    "values": [],
    "reasoning": ""
  },
  "verticality": {
    "is_horizontal": null,
    "vertical_sectors_impacted": [],
    "reasoning": ""
  },
  "beneficiary_archetype": {
    "values": [],
    "reasoning": ""
  },
  "subject_expertise": {
    "domains": [],
    "specialisations": [],
    "reasoning": ""
  }
}"""


def build_classification_prompt(*, company_description: str, today: str) -> str:
    """Fill the two template tokens, mirroring the n8n *AI Agent3* node.

    ``company_description`` is the company's ``Company description`` field (the
    n8n node read exactly that — not the country-prefixed sanity-check profile).
    """
    return COMPANY_CLASSIFICATION_INSTRUCTIONS.replace(
        "__COMPANY_DESCRIPTION__", company_description
    ).replace("__TODAY__", today)
