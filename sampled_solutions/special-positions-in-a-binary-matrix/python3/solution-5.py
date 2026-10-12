def equal_vowel_count(input_str):
    vowels = "aeiou"
    mid = len(input_str) // 2
    count1 = count_vowels(input_str[:mid], vowels)
    count2 = count_vowels(input_str[mid:], vowels)
    
    return count1 == count2

def count_vowels(s, vowels):
    count = 0
    for char in s:
        if char.lower() in vowels:
            count += 1
    return count

# Test the function
input_str = "examplestring"
result = equal_vowel_count(input_str)
print(result)  # Output: True or False