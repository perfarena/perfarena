from collections import defaultdict

def num_weak_characters(properties):
    groups = defaultdict(list)
    min_attack = 10**5 + 1
    max_attack = 0
    for attack, defense in properties:
        min_attack = min(min_attack, attack)
        max_attack = max(max_attack, attack)
        groups[attack].append(defense)
    
    count = 0
    max_defense = -1
    for i in range(max_attack, min_attack - 1, -1):
        if i not in groups:
            continue
        for defense in groups[i]:
            if defense < max_defense:
                count += 1
        max_defense = max(max_defense, max(groups[i], default=-1))
    
    return count

# Test the function with properties array
properties = [[1, 5], [10, 4], [4, 3]]
result = num_weak_characters(properties)
print(result)  # Output should be the number of weak characters