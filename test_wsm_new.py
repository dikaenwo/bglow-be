from recommender import _score_product_wsm, _parse_ingredients

ing1 = 'Aqua, Niacinamide, Propanediol, Butylene Glycol, Panthenol, Propylene Glycol, Glycereth-26, Phenoxyethanol, Acrylates/C10-30 Alkyl Acrylate Crosspolymer, Tranexamic Acid, Allantoin, Disodium EDTA, Trehalose, Triethylene Glycol, Caprylic/Capric Triglyceride, Glycerin, Hydrogenated Lecithin, Triethanolamine, Glycyrrhiza Glabra Root Extract, 1,2-Hexanediol, Sucrose Stearate, Ceramide NP, Ceramide AP, Ceramide AS, Ceramide NG, Glycosphingolipids'

ing2 = 'Aqua, Glycerin, Sodium Cocoyl Glycinate, Lauramidopropyl Betaine, Disodium Cocoyl Glutamate, Betaine, Hydroxypropyl Starch Phosphate, Niacinamide, Citric Acid, Phenoxyethanol, Salicylic Acid, Allantoin, Fragrance, Disodium EDTA, Triethylene Glycol, Melaleuca Alternifolia (Tea Tree) Leaf Oil, Ci 42090, Ci 19140'

for label, ing_text in [('Hanasui Bright Moisturizer', ing1), ('Hanasui Acne FW', ing2)]:
    ings = _parse_ingredients(ing_text)
    res = _score_product_wsm(ings, 'berminyak', {'berjerawat'})
    match_pct = min(100, max(0, round((res['wsm_score'] - 0.5) * 200)))
    print(label)
    print('  N=%d | wsm=%.3f | match=%d%% | %s' % (len(ings), res['wsm_score'], match_pct, res['kategori_rekomendasi']))
    print('  C1=%.3f C2=%.3f C3=%.3f' % (res['C1'], res['C2'], res['C3']))
    print('  cocok=%s' % res['cocok_names'][:4])
    print('  tidak=%s' % res['tidak_names'][:3])
    print()
