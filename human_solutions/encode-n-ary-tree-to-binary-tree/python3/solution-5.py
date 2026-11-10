def count_matching_stones(jewels, stones):
    stone_set = set(jewels)
    count = 0
    
    for stone in stones:
        if stone in stone_set:
            count += 1
    
    return count

# Example usage
jewels = "aA"
stones = "aAAbbbb"
print(count_matching_stones(jewels, stones))  # Output: 3