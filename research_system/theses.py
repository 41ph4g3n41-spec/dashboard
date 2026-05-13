"""Investment thesis per portfolio company.

Initial drafts — edit freely. These get injected as context into every
Claude analysis call so the model reads news through *our* lens.
"""

THESES = {
    "ETERNAL": {
        "name": "Eternal (Zomato)",
        "thesis": (
            "Two-platform compounder: food delivery (Zomato) is now a "
            "rational duopoly with Swiggy, generating sustainable margins, "
            "while quick commerce (Blinkit) is the asymmetric option — "
            "category leader in a market that could 5x. Net cash, founder-"
            "led, optionality on going-out (District) and B2B (Hyperpure)."
        ),
        "watch": [
            "Blinkit dark store count, GOV/store, contribution margin trajectory",
            "Food delivery take rate and adjusted EBITDA margin (target ~5% GOV)",
            "Competitive intensity from Zepto / Instamart / Flipkart Minutes",
            "Cash burn at Blinkit vs guidance",
            "Regulatory: gig worker rules, FDI in inventory-led ecomm",
        ],
        "breaks": [
            "Food delivery growth structurally <15% with no margin expansion",
            "Blinkit contribution margin reverses negative for 2+ quarters",
            "Aggressive new entrant burns Blinkit's unit economics",
            "Founder/key talent exit",
        ],
        "catalyst": "Quarterly Blinkit store + AOV print; QC market share data",
    },
    "FIRSTCRY": {
        "name": "Brainbees Solutions (FirstCry)",
        "thesis": (
            "Vertical specialist with the deepest babycare assortment in India, "
            "blended online + ~1,000 offline stores. Captive brand (BabyHug) "
            "drives margin. Long runway as organised babycare penetration is "
            "<10%. Globalsoft ARR (international) is the optional kicker."
        ),
        "watch": [
            "India multichannel GMV growth and contribution margin",
            "BabyHug share of GMV (private label margin lever)",
            "Store rollout pace and unit economics by cohort",
            "Globalsoft (Middle East) growth and burn",
            "Working capital cycle / inventory days",
        ],
        "breaks": [
            "Sustained negative contribution margin in India multichannel",
            "Private label share stalls below 30%",
            "Heavy discounting from Amazon/Flipkart in babycare",
        ],
        "catalyst": "Quarterly segment disclosure; BabyHug mix",
    },
    "HATSUN": {
        "name": "Hatsun Agro Product",
        "thesis": (
            "South India's largest private dairy with farm-gate procurement "
            "moat in TN/KA/AP. Value-added dairy (Arun ice cream, Hatsun "
            "curd/paneer/ghee) is the margin story; liquid milk is the "
            "volume base. Founder-promoter aligned. Capex cycle moderating, "
            "FCF inflecting."
        ),
        "watch": [
            "Value-added dairy share of revenue (>40% is the unlock)",
            "Milk procurement price vs realisation spread",
            "Arun ice cream parlour rollout and same-store sales",
            "Net debt / EBITDA trajectory",
            "South India monsoon and fodder costs",
        ],
        "breaks": [
            "VADP share stagnates; gross margin compresses 2+ quarters",
            "Amul/Nandini gains share in core southern markets",
            "Sustained ROCE below 12%",
        ],
        "catalyst": "Quarterly VADP mix; festive ice cream season",
    },
    "PAYTM": {
        "name": "One 97 Communications (Paytm)",
        "thesis": (
            "Survivor-mode fintech post the PPBL shock. Distribution scale "
            "(merchants, soundbox) is real; monetisation now via merchant "
            "loans (distribution-only model with banking partners) and "
            "device subscriptions. Net cash cushion, contribution margin "
            "positive. Optionality: payments aggregator licence usage, UPI "
            "incentive normalisation."
        ),
        "watch": [
            "Monthly transacting users and merchant base",
            "Loan disbursals (merchant + personal) and DLG model health",
            "Contribution margin and quarterly cash burn",
            "Regulatory posture: RBI, ED, MeitY, payments aggregator status",
            "UPI market share and any MDR / incentive policy change",
        ],
        "breaks": [
            "Regulatory action that limits new merchant onboarding again",
            "Loan partners pull back; disbursals fall 2 quarters in a row",
            "Cash burn re-accelerates beyond Rs 200 cr/quarter",
        ],
        "catalyst": "Quarterly disbursal mix; any RBI communication",
    },
    "PANACEA": {
        "name": "Panacea Biotec",
        "thesis": (
            "Re-rated vaccine play — fully-liquid hexavalent EasySix and "
            "WHO-prequalified pentavalent give it a seat at UNICEF / Gavi "
            "tenders few Indian peers have. Pharma formulations are the "
            "drag; vaccines + capacity expansion + clean balance sheet "
            "post real-estate monetisation is the setup."
        ),
        "watch": [
            "UNICEF / Gavi tender wins and shipment cadence",
            "Hexavalent ramp and capacity utilisation",
            "Net debt trajectory; any further land monetisation",
            "Pharma division losses",
            "WHO PQ status / audit outcomes",
        ],
        "breaks": [
            "WHO PQ adverse finding",
            "Loss of a major tender (UNICEF / PAHO)",
            "Pharma losses accelerate",
        ],
        "catalyst": "Tender announcements; quarterly vaccine volume",
    },
    "PICCADIL": {
        "name": "Piccadily Agro Industries",
        "thesis": (
            "Indri single-malt is the trojan horse — premiumisation of "
            "Indian whisky, global awards, export pull. Backward-integrated "
            "(own distillery, grain neutral spirits) gives gross margin "
            "cover. Sugar / ENA business is the cash cow funding the "
            "premium spirits ramp."
        ),
        "watch": [
            "Indri case volumes (domestic + export) and ASP",
            "Premium spirits gross margin (>50% sustained)",
            "Distillery capacity expansion / barrel inventory build",
            "Sugar segment EBITDA stability",
            "Regulatory: state excise, import duty changes on Scotch",
        ],
        "breaks": [
            "Indri growth flattens for 2 quarters in core markets",
            "Aggressive Pernod/Diageo response on pricing",
            "Sugar segment swings to losses on cane cost",
        ],
        "catalyst": "Quarterly Indri volume disclosure; export wins",
    },
    "SAPPHIRE": {
        "name": "Sapphire Foods India",
        "thesis": (
            "Largest KFC franchisee + Pizza Hut operator in India (+ Sri "
            "Lanka). KFC unit economics among best in industry; new store "
            "ROIC strong. Pizza Hut turnaround optionality. Levered to "
            "discretionary recovery + premiumisation in QSR chicken."
        ),
        "watch": [
            "KFC same-store sales growth (SSSG) and store ROI",
            "Pizza Hut SSSG and restaurant EBITDA margin",
            "Net store additions guidance",
            "Input costs: chicken, cheese, palm oil",
            "Competitive: Devyani, Jubilant, regional QSR",
        ],
        "breaks": [
            "KFC SSSG turns negative 2 quarters in a row",
            "Pizza Hut margins fail to inflect after refresh",
            "Net new-store ROIC drops below cost of capital",
        ],
        "catalyst": "Quarterly SSSG, restaurant EBITDA margin",
    },
    "STARHEAL": {
        "name": "Star Health & Allied Insurance",
        "thesis": (
            "Largest standalone health insurer with retail agency moat. "
            "Combined ratio normalising post-COVID; pricing actions taken. "
            "Long structural: India health insurance penetration <5%, "
            "rising lifestyle disease load, government push (Ayushman). "
            "Valuation re-rates as claims ratio stabilises around 65%."
        ),
        "watch": [
            "Gross written premium growth (retail health)",
            "Claims ratio (target 65-66%) and combined ratio",
            "Solvency ratio (>1.7x) and investment book yields",
            "Agent productivity and digital channel mix",
            "IRDAI policy: composite licence, EoM, pricing freedom",
        ],
        "breaks": [
            "Claims ratio sustained above 70%",
            "Loss of agent network share to ICICI Lombard / Niva Bupa",
            "Adverse IRDAI ruling on retail health pricing",
        ],
        "catalyst": "Quarterly claims ratio; monthly GWP data",
    },
    "SUNTV": {
        "name": "Sun TV Network",
        "thesis": (
            "South Indian regional media monopoly with high cash conversion "
            "and zero debt. Linear TV is declining slowly; Sun NXT (OTT), "
            "movies (Sun Pictures), and IPL (Sunrisers Hyderabad) provide "
            "optionality. Dividend yield + buyback discipline make it a "
            "yield + optionality stock."
        ),
        "watch": [
            "Subscription revenue trajectory and ARPU",
            "Sun NXT subscribers and content slate",
            "Sun Pictures movie release pipeline / hits",
            "IPL media rights cycle and SRH valuation",
            "Capital return policy (dividend + buyback)",
        ],
        "breaks": [
            "Linear TV revenue declines >10% YoY for 2+ quarters",
            "Sun NXT subs stagnate; content slate weak",
            "Capital misallocation into new ventures",
        ],
        "catalyst": "Quarterly subs/ad-rev split; movie box-office",
    },
}


def thesis_for(ticker: str) -> dict | None:
    return THESES.get(ticker.upper())
