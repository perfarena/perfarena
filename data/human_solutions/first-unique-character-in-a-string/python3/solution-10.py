class Placeholder:
    def __init__(self, start, end):
        self.start = start
        self.end = end

def first_unique_char_index(s):
    char_map = {}
    for i, char in enumerate(s):
        if char not in char_map:
            char_map[char] = Placeholder(i, i)
        else:
            char_map[char].end = i

    min_index = float('inf')
    for char, pos in char_map.items():
        if pos.start == pos.end and pos.start < min_index:
            min_index = pos.start

    return min_index if min_index != float('inf') else -1

# Test the function with a sample string
s = "leetcode"
result = first_unique_char_index(s)
print(result)  # Output: 0