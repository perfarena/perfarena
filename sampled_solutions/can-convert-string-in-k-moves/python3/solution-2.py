def minDeletions(s):
    characters = {}
    for char in s:
        characters[char] = characters.get(char, 0) + 1
    
    unique_set = set()
    count = 0
    
    for freq in characters.values():
        while freq > 0 and freq in unique_set:
            freq -= 1
            count += 1
        unique_set.add(freq)
    
    return count

# Example usage
input_str = "aabbccc"
print(minDeletions(input_str))  # Output: 2