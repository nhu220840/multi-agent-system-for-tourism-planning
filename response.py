from app.graph.build_graph import run_travel_graph

q = "lich trinh 3 ngay o Da Nang, uu tien bao tang va dia diem co view dep, khong can di choi ban dem, chi can di choi ban ngay, va toi uu chi phi"
res = run_travel_graph(q, top_k=16, with_plan=True)

print("=== ANSWER FORMAT ===")
print(res.answer)

print("\n=== FOLLOW UP QUESTIONS ===")
print(res.follow_up_questions)

print("\n=== RAW FIELDS ===")
print("recommended_hotel:", res.recommended_hotel)
print("stay_plan:", res.stay_plan)
print("route_plan:", res.route_plan[:2] if res.route_plan else [])