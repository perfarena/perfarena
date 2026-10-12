def is_vowel(char):
    vowels = {'A', 'E', 'I', 'O', 'U', 'a', 'e', 'i', 'o', 'u'}
    return char in vowels

def equal_vowels_on_both_sides(s):
    left_count = 0
    n = len(s)
    mid = n // 2

    for i in range(mid):
        if is_vowel(s[i]):
            left_count += 1

    right_count = 0
    for i in range(mid + 1, n):
        if is_vowel(s[i]):
            right_count += 1

    return left_count == right_count

# Test the function
input_str = "examplestring"
result = equal_vowels_on_both_sides(input_str)
print(result)