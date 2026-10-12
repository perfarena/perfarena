def find_smallest_character(letters, target):
    for char in letters:
        if char > target:
            return char
    return letters[0]

# Test the function
letters = ['C', 'F', 'J']
target = 'A'
result = find_smallest_character(letters, target)
print(result)