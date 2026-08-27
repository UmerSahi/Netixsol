"""
RealEstate Hub -- Knowledge Base Builder (single source of truth)
====================================================================
Rebuilds the ENTIRE knowledge base from scratch, from the original raw
listings export, for ONE real estate agency ("RealEstate Hub") -- not a
multi-agency marketplace. Run this whenever you want a clean rebuild.

Usage:
    python build_knowledge_base.py [path_to_raw_csv]
    (defaults to ./Property_with_Feature_Engineering.csv if no arg given)

Produces (all in the current directory):
    properties.csv    - 750 real listings, filtered to Lahore/Islamabad/
                         Rawalpindi, single-agency (no agency column),
                         each assigned to one of RealEstate Hub's own agents
    agents.csv         - RealEstate Hub's in-house agent roster
    locations.csv       - locality profiles (city, description)
    amenities.csv       - per-locality amenities (dummy, realistic pool)
    schools.csv          - real, named nearby schools per locality (researched)
    hospitals.csv         - real, named nearby hospitals per locality (researched)
    developers.csv         - REAL housing developers/authorities per locality
                             (DHA, CDA, LDA, Bahria Town Pvt Ltd, etc.) --
                             this is who built each society, not a competing
                             agency, since RealEstate Hub is the only agency
                             in this system
    payment_plans.csv       - typical installment structures (dummy, PK norms)
    faqs.csv                  - standard process FAQs (dummy)
    realestate_kb.db            - SQLite snapshot of all 9 tables (for local/
                                   no-server use; see postgres_version/ for
                                   loading the same tables into Postgres)
"""
import sys
import random
import pandas as pd
from sqlalchemy import text
from config import get_engine, DB_BACKEND

random.seed(11)
SOURCE_CSV = sys.argv[1] if len(sys.argv) > 1 else "Property_with_Feature_Engineering.csv"
CITIES = ["Lahore", "Islamabad", "Rawalpindi"]


def build_properties():
    df = pd.read_csv(SOURCE_CSV)
    sub = df[df['city'].isin(CITIES)].copy()
    sub = sub[(sub['price'] > 0) & (sub['area_marla'] > 0) & (sub['area_marla'] < 1000)]
    sub = sub.drop_duplicates(subset=['location_id', 'property_type', 'price', 'area_marla', 'bedrooms', 'purpose'])

    samples = []
    for city in CITIES:
        city_df = sub[sub['city'] == city]
        n = min(250, len(city_df))
        samples.append(city_df.sample(n=n, random_state=42))
    final = pd.concat(samples).reset_index(drop=True)
    final['property_id'] = ['PROP-' + str(i + 1000) for i in range(len(final))]

    cols = ['property_id', 'property_type', 'purpose', 'city', 'locality', 'location',
            'price', 'price_bin', 'area_marla', 'area_sqft', 'bedrooms', 'baths',
            'latitude', 'longitude', 'date_added']
    final = final[cols]

    # ---- single-agency: assign every listing to an in-house RealEstate Hub agent ----
    agent_roster = [
        {"agent_id": "AG-01", "agent_name": "Ahmed Raza",     "base_city": "Lahore"},
        {"agent_id": "AG-02", "agent_name": "Sana Malik",     "base_city": "Lahore"},
        {"agent_id": "AG-03", "agent_name": "Bilal Chaudhry", "base_city": "Lahore"},
        {"agent_id": "AG-04", "agent_name": "Ayesha Farooq",  "base_city": "Islamabad"},
        {"agent_id": "AG-05", "agent_name": "Usman Tariq",    "base_city": "Islamabad"},
        {"agent_id": "AG-06", "agent_name": "Hina Shaikh",    "base_city": "Islamabad"},
        {"agent_id": "AG-07", "agent_name": "Faisal Mehmood", "base_city": "Rawalpindi"},
        {"agent_id": "AG-08", "agent_name": "Mahnoor Iqbal",  "base_city": "Rawalpindi"},
    ]
    agents_df = pd.DataFrame(agent_roster)

    def assign(city):
        pool = agents_df[agents_df["base_city"] == city]["agent_id"].tolist()
        return random.choice(pool) if pool else random.choice(agents_df["agent_id"].tolist())

    final["agent_id"] = final["city"].apply(assign)
    final = final.merge(agents_df[["agent_id", "agent_name"]], on="agent_id", how="left")
    final = final.rename(columns={"agent_name": "agent"})
    final.to_csv("properties.csv", index=False)

    counts = final['agent_id'].value_counts().to_dict()
    agents_df["active_listings"] = agents_df["agent_id"].map(counts).fillna(0).astype(int)
    agents_df["phone_ext"] = ["101", "102", "103", "201", "202", "203", "301", "302"]
    agents_df.to_csv("agents.csv", index=False)
    return final


def build_locations_and_kb(props):
    top_localities = props['locality'].value_counts().head(18).index.tolist()
    locality_info = {
        "Bahria Town Rawalpindi, Rawalpindi, Punjab": ("Rawalpindi", "Bahria Town", "Large gated community, Phases 1-8, popular with overseas Pakistanis and families seeking modern infrastructure."),
        "DHA Defence, Lahore, Punjab": ("Lahore", "DHA Defence", "Premium gated cantonment-adjacent society, Phases 1-12, high resale value, strong security."),
        "E-11, Islamabad, Islamabad Capital": ("Islamabad", "E-11", "Upscale sector near Margalla Hills, mix of houses and modern apartment towers."),
        "DHA Defence, Islamabad, Islamabad Capital": ("Islamabad", "DHA Defence Islamabad", "Newer DHA phases along Islamabad Expressway, popular with young families."),
        "Bahria Town, Islamabad, Islamabad Capital": ("Islamabad", "Bahria Town Islamabad", "Extension of Bahria network on the Islamabad side of the Expressway."),
        "Johar Town, Lahore, Punjab": ("Lahore", "Johar Town", "Centrally located middle-to-upper-income residential area, close to Emporium Mall."),
        "F-11, Islamabad, Islamabad Capital": ("Islamabad", "F-11", "Established elite sector, tree-lined streets, walking distance to Centaurus Mall."),
        "F-10, Islamabad, Islamabad Capital": ("Islamabad", "F-10", "One of Islamabad's oldest premium sectors, close to Kohsar Market and F-10 Markaz."),
        "G-11, Islamabad, Islamabad Capital": ("Islamabad", "G-11", "Well-planned middle-income sector, close to G-11 Markaz."),
        "Ghauri Town, Islamabad, Islamabad Capital": ("Islamabad", "Ghauri Town", "Affordable housing scheme on Islamabad Expressway, popular with first-time buyers."),
        "I-10, Islamabad, Islamabad Capital": ("Islamabad", "I-10", "Industrial-adjacent sector with affordable housing and government-sector residents."),
        "Airport Housing Society, Rawalpindi, Punjab": ("Rawalpindi", "Airport Housing Society", "Close to Islamabad International Airport, growing residential demand."),
        "G-15, Islamabad, Islamabad Capital": ("Islamabad", "G-15", "Newer developing sector on the western side of Islamabad."),
        "G-13, Islamabad, Islamabad Capital": ("Islamabad", "G-13", "Mid-range sector, close to G-13 Markaz and NUST university."),
        "F-6, Islamabad, Islamabad Capital": ("Islamabad", "F-6", "Central diplomatic-enclave-adjacent sector, very high resale value."),
        "Faisal Town, Lahore, Punjab": ("Lahore", "Faisal Town", "Established mid-income residential area near Punjab University."),
        "Model Town, Lahore, Punjab": ("Lahore", "Model Town", "One of Lahore's oldest planned societies, known for its central park."),
        "College Road, Lahore, Punjab": ("Lahore", "College Road", "Mixed residential-commercial corridor near Township."),
    }
    locations_rows = [{"locality_full": k, "city": v[0], "locality_name": v[1], "description": v[2]}
                       for k, v in locality_info.items()]
    locations_df = pd.DataFrame(locations_rows)
    locations_df.to_csv("locations.csv", index=False)

    amenity_pool = ["24/7 Security", "Gated Community", "Community Park", "Mosque",
                     "Underground Electricity", "Water Filtration Plant", "Gymnasium",
                     "Swimming Pool", "Commercial Plaza Nearby", "Wide Carpeted Roads",
                     "Sewerage System", "Playground", "Mini Golf / Sports Complex",
                     "CCTV Surveillance", "Solar Street Lights"]
    amenities_rows = []
    for loc in locality_info:
        n = random.randint(6, 10)
        for a in random.sample(amenity_pool, n):
            amenities_rows.append({"locality_full": loc, "amenity": a})
    pd.DataFrame(amenities_rows).to_csv("amenities.csv", index=False)

    schools_rows = [
        ("DHA Defence, Lahore, Punjab", "Lahore Grammar School DHA", "O/A Level", 1.5),
        ("DHA Defence, Lahore, Punjab", "Beaconhouse DHA Campus", "O/A Level", 2.0),
        ("DHA Defence, Lahore, Punjab", "The City School DHA Phase 3", "O/A Level", 2.3),
        ("Bahria Town Rawalpindi, Rawalpindi, Punjab", "Beaconhouse Bahria Town Campus", "O/A Level", 1.2),
        ("Bahria Town Rawalpindi, Rawalpindi, Punjab", "Roots Millennium Bahria Campus", "O/A Level", 1.8),
        ("Bahria Town, Islamabad, Islamabad Capital", "Bahria College Islamabad", "Matric/A-Level", 2.0),
        ("F-10, Islamabad, Islamabad Capital", "Islamabad Model College F-10/3", "Matric", 1.0),
        ("F-10, Islamabad, Islamabad Capital", "Beaconhouse F-10 Campus", "O/A Level", 1.4),
        ("F-11, Islamabad, Islamabad Capital", "Roots Ivy International F-11", "O/A Level", 1.6),
        ("F-6, Islamabad, Islamabad Capital", "Froebel's International School F-6", "O/A Level", 1.3),
        ("E-11, Islamabad, Islamabad Capital", "Islamabad Convent School", "Matric", 3.0),
        ("G-11, Islamabad, Islamabad Capital", "CDA Model School G-11", "Matric", 1.1),
        ("G-13, Islamabad, Islamabad Capital", "Islamabad College for Boys G-6 (nearby)", "Matric/FSc", 3.5),
        ("Johar Town, Lahore, Punjab", "The Learning Alliance Johar Town", "O/A Level", 2.1),
        ("Model Town, Lahore, Punjab", "Aitchison College (nearest elite option)", "O/A Level", 4.0),
        ("Faisal Town, Lahore, Punjab", "The City School Faisal Town", "O/A Level", 1.7),
    ]
    pd.DataFrame(schools_rows, columns=["locality_full", "school_name", "level", "distance_km_est"]).to_csv("schools.csv", index=False)

    hospitals_rows = [
        ("DHA Defence, Lahore, Punjab", "Bahria International Hospital DHA", "Multi-specialty", 2.2),
        ("DHA Defence, Lahore, Punjab", "Farooq Hospital DHA Branch", "Multi-specialty, 24/7 ER", 2.5),
        ("Bahria Town Rawalpindi, Rawalpindi, Punjab", "Bahria Town Hospital", "Multi-specialty incl. Cardiac", 1.0),
        ("Bahria Town Rawalpindi, Rawalpindi, Punjab", "Kulsum International Hospital (Bahria access)", "General", 3.0),
        ("Bahria Town, Islamabad, Islamabad Capital", "Shifa International Hospital (nearest tertiary)", "Tertiary care", 6.0),
        ("F-10, Islamabad, Islamabad Capital", "Maroof International Hospital", "Multi-specialty", 2.0),
        ("F-6, Islamabad, Islamabad Capital", "Ali Medical Centre", "Multi-specialty", 1.8),
        ("F-11, Islamabad, Islamabad Capital", "Quaid-e-Azam International Hospital", "Multi-specialty", 2.4),
        ("E-11, Islamabad, Islamabad Capital", "Shifa International Hospital", "Tertiary care", 4.5),
        ("G-11, Islamabad, Islamabad Capital", "Islamabad Diagnostic Center (IDC)", "Diagnostics/OPD", 1.5),
        ("G-13, Islamabad, Islamabad Capital", "Ali Medical Centre G-8 (nearest)", "Multi-specialty", 3.2),
        ("Johar Town, Lahore, Punjab", "Doctors Hospital Johar Town", "Multi-specialty", 1.9),
        ("Model Town, Lahore, Punjab", "Shaikh Zayed Hospital", "Tertiary care", 3.5),
        ("Faisal Town, Lahore, Punjab", "Ittefaq Hospital", "General", 2.6),
        ("Airport Housing Society, Rawalpindi, Punjab", "Benazir Bhutto Hospital", "Teaching hospital", 5.0),
        ("Ghauri Town, Islamabad, Islamabad Capital", "Pak Emirates Military Hospital (nearest tertiary)", "Tertiary care", 7.0),
    ]
    pd.DataFrame(hospitals_rows, columns=["locality_full", "hospital_name", "specialty", "distance_km_est"]).to_csv("hospitals.csv", index=False)

    # ---- REAL developers/housing authorities (single-agency model: these are
    # not competing agencies, they're who built/regulates each society) ----
    developer_map = {
        ("Bahria Town", "Rawalpindi"): ("Bahria Town Pvt. Ltd.", "Private developer; master-planned gated community with company-managed maintenance and security across all phases."),
        ("DHA Defence", "Lahore"): ("Defence Housing Authority (DHA) Lahore", "Semi-government housing authority run by the armed forces welfare trust; regulates construction bylaws and transfers directly."),
        ("E-11", "Islamabad"): ("Capital Development Authority (CDA)", "Federal government authority that planned and allots Islamabad's sector grid, including E-11."),
        ("DHA Defence Islamabad", "Islamabad"): ("Defence Housing Authority (DHA) Islamabad-Rawalpindi", "Semi-government housing authority; newer DHA phases along the Islamabad Expressway."),
        ("Bahria Town Islamabad", "Islamabad"): ("Bahria Town Pvt. Ltd.", "Private developer; Islamabad-side extension of the Bahria Town network along the Islamabad Expressway."),
        ("Johar Town", "Lahore"): ("Lahore Development Authority (LDA)", "Provincial development authority responsible for planning and infrastructure in Johar Town."),
        ("F-11", "Islamabad"): ("Capital Development Authority (CDA)", "Federal authority; one of Islamabad's original planned sectors."),
        ("F-10", "Islamabad"): ("Capital Development Authority (CDA)", "Federal authority; one of Islamabad's oldest premium sectors."),
        ("G-11", "Islamabad"): ("Capital Development Authority (CDA)", "Federal authority overseeing sector development and utilities."),
        ("Ghauri Town", "Islamabad"): ("Ghauri Town (Pvt) Ltd.", "Private housing scheme developer on the Islamabad Expressway corridor."),
        ("I-10", "Islamabad"): ("Capital Development Authority (CDA)", "Federal authority; industrial-adjacent sector."),
        ("Airport Housing Society", "Rawalpindi"): ("Airport Housing Society (Cooperative)", "Cooperative housing society for aviation-sector employees, self-managed with its own society office."),
        ("G-15", "Islamabad"): ("Capital Development Authority (CDA)", "Federal authority; newer developing western sector."),
        ("G-13", "Islamabad"): ("Capital Development Authority (CDA)", "Federal authority; mid-range planned sector."),
        ("F-6", "Islamabad"): ("Capital Development Authority (CDA)", "Federal authority; central diplomatic-enclave-adjacent sector."),
        ("Faisal Town", "Lahore"): ("Lahore Development Authority (LDA)", "Provincial authority; established mid-income planned area."),
        ("Model Town", "Lahore"): ("Model Town Society (Cooperative)", "One of the subcontinent's oldest cooperative housing societies, self-governed since 1922."),
        ("College Road", "Lahore"): ("Lahore Development Authority (LDA)", "Provincial authority; mixed residential-commercial corridor."),
    }
    dev_rows = []
    for _, loc in locations_df.iterrows():
        key = (loc['locality_name'], loc['city'])
        if key in developer_map:
            dev, desc = developer_map[key]
            dev_rows.append({"developer_authority": dev, "locality_name": loc['locality_name'],
                              "city": loc['city'], "profile": desc})
    pd.DataFrame(dev_rows).to_csv("developers.csv", index=False)

    payment_plans_rows = [
        {"plan_name": "Standard 3-Year Installment Plan", "applicable_to": "New bookings / developer plots",
         "down_payment_pct": 20, "confirmation_pct": 10, "quarterly_installments": 10,
         "installment_pct_each": 6, "possession_charges_pct": 10,
         "notes": "Typical structure offered on developer-led projects (e.g., Bahria Town, DHA new phases); exact terms vary by project and must be confirmed with RealEstate Hub before booking."},
        {"plan_name": "2-Year Easy Plan", "applicable_to": "New bookings",
         "down_payment_pct": 25, "confirmation_pct": 0, "quarterly_installments": 8,
         "installment_pct_each": 8.5, "possession_charges_pct": 7,
         "notes": "Shorter tenure, higher down payment; common on smaller/affordable housing schemes."},
        {"plan_name": "Full Cash / Ready Property", "applicable_to": "Resale properties (most listings in this KB)",
         "down_payment_pct": 100, "confirmation_pct": 0, "quarterly_installments": 0,
         "installment_pct_each": 0, "possession_charges_pct": 0,
         "notes": "Most properties in this knowledge base are resale/ready listings paid in full at transfer; installment plans apply mainly to new developer bookings and should not be assumed available unless confirmed."},
    ]
    pd.DataFrame(payment_plans_rows).to_csv("payment_plans.csv", index=False)

    faqs_rows = [
        ("What documents are required to buy a property?", "Buying", "CNIC copies of buyer and seller, original property documents (registry/intiqal), NOC from the relevant housing authority (e.g., DHA, Bahria Town, CDA), and a token/agreement to sell."),
        ("How much token money is usually paid to book a property?", "Buying", "Token amounts are negotiated case by case, typically a small percentage of the sale price, and are adjusted into the final payment; exact figures depend on the agreement between buyer and seller."),
        ("Can foreigners or overseas Pakistanis buy property in these areas?", "Buying", "Overseas Pakistanis can generally buy property in Pakistan through legal representatives or in person; specific requirements should be confirmed with the developer/authority for each society."),
        ("What is the difference between 'For Sale' and 'For Rent' listings?", "General", "'For Sale' listings are for outright purchase; 'For Rent' listings are for leasing, typically with a security deposit and monthly/annual rent as agreed with the landlord."),
        ("Are utility bills included in rent?", "Renting", "This varies by landlord and property; it should be confirmed and stated explicitly in the rental agreement before move-in."),
        ("What is the standard security deposit for rentals?", "Renting", "Security deposits commonly range from 1-2 months' rent, but this is negotiable between landlord and tenant."),
        ("Is the price shown final or negotiable?", "General", "Listed prices are generally a starting point for negotiation; final price depends on discussion between buyer/tenant and seller/landlord or their RealEstate Hub agent."),
        ("How is plot size measured (marla vs sq ft)?", "General", "1 Marla = 225 sq ft (approx., varies slightly by region); the knowledge base stores both area_marla and area_sqft for each property."),
        ("Can I get a property visit scheduled the same day?", "Booking", "Same-day visits are subject to agent and property availability; the assistant will check real-time slot availability before confirming."),
        ("What happens if I miss my scheduled visit?", "Booking", "The visit can be rescheduled at no cost; please inform your RealEstate Hub agent as early as possible so the slot can be released for other clients."),
        ("Do you offer investment consultancy?", "Investment", "The assistant can share historical price trends and rental yield estimates drawn from the knowledge base; it does not provide guaranteed return projections or financial advice."),
        ("Is DHA or Bahria Town a better investment?", "Investment", "This depends on budget, goals, and risk tolerance; the assistant can share historical trend data for both but does not make guarantees about future appreciation."),
        ("What are the transfer/registration charges?", "Buying", "Transfer charges vary by housing authority and property type (typically a percentage of DC value or sale price); exact current rates should be confirmed with the relevant authority at the time of transfer."),
        ("Can the assistant negotiate price on my behalf?", "General", "The assistant can relay offers to your assigned RealEstate Hub agent but cannot finalize a negotiated price; final agreement is between buyer/tenant and seller/landlord."),
        ("How often is the property database updated?", "General", "Listings in this knowledge base reflect the dataset snapshot used for this project; in production this would sync with RealEstate Hub's live inventory on a regular schedule."),
        ("What property types are available?", "General", "The knowledge base covers Houses, Flats, Upper/Lower Portions, Farm Houses, Penthouses, and Rooms, across Lahore, Islamabad, and Rawalpindi."),
        ("Is financing/mortgage assistance available?", "Buying", "The assistant does not process financing directly; it can note that bank mortgage products exist in the market but cannot recommend or guarantee approval terms."),
        ("How do I cancel a scheduled appointment?", "Booking", "You can request a cancellation at any time before the visit; the assistant will confirm the cancellation and update the calendar and CRM record."),
        ("Are commercial properties included in this knowledge base?", "General", "This knowledge base snapshot focuses on residential listings (House, Flat, Portion, Farm House, Penthouse); commercial inventory would be a separate dataset in a full production system."),
        ("What if a property I ask about is not in the database?", "General", "The assistant will say it does not have verified information rather than guessing, and can offer to note the request for a human RealEstate Hub agent to follow up."),
    ]
    pd.DataFrame(faqs_rows, columns=["question", "category", "answer"]).to_csv("faqs.csv", index=False)


def load_into_database():
    """Loads every CSV into whichever database config.py resolves to
    (SQLite or Postgres, per DB_BACKEND in .env) -- no code branching here."""
    engine = get_engine()
    tables = ["properties", "locations", "amenities", "schools", "hospitals",
              "developers", "payment_plans", "faqs", "agents"]
    with engine.begin() as conn:
        for t in tables:
            pd.read_csv(t + ".csv").to_sql(t, conn, if_exists="replace", index=False)
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_prop_city ON properties(city)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_prop_locality ON properties(locality)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_prop_purpose ON properties(purpose)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_prop_agent ON properties(agent_id)"))
    with engine.connect() as conn:
        for t in tables:
            n = conn.execute(text(f"SELECT COUNT(*) FROM {t}")).scalar()
            print(f"  {t}: {n} rows")


if __name__ == "__main__":
    print(f"Building knowledge base from: {SOURCE_CSV}")
    props = build_properties()
    print(f"  properties.csv: {len(props)} rows (single-agency: RealEstate Hub)")
    build_locations_and_kb(props)
    print(f"Loading into database (DB_BACKEND={DB_BACKEND})...")
    load_into_database()
    print("Done.")
