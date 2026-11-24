from collections import Counter

def min_deletions_to_make_good(s):
    counter = Counter(s)
    values = sorted(counter.values())
    taken_values = set()
    total_deletions = 0
    
    for value in values:
        while value in taken_values:
            value -= 1
            total_deletions += 1
        if value > 0:
            taken_values.add(value)
    
    return total_deletions

# Example usage
s = "aabbccc"
result = min_deletions_to_make_good(s)
print(result)  # Output: 2