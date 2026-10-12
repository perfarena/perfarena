def count_weak_characters(properties):
    properties.sort(key=lambda x: (-x[0], x[1]))  # Sort by attack descending, defense ascending
    answer = 0
    max_defense = 0
    
    for attack, defense in properties:
        if defense < max_defense:
            answer += 1
            max_defense = max(max_defense, defense)
    
    return answer

# Example usage
properties = [[1, 7], [1, 6], [2, 5], [2, 4], [1, 3], [1, 2], [1, 1]]
print(count_weak_characters(properties))  # Output: 3