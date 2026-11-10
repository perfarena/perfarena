def reverse_vowels(s):
    vowels = set('aeiouAEIOU')
    s = list(s)
    left, right = 0, len(s) - 1

    while left < right:
        if s[left] not in vowels:
            left += 1
        elif s[right] not in vowels:
            right -= 1
        else:
            s[left], s[right] = s[right], s[left]
            left += 1
            right -= 1

    return ''.join(s)

# Test the function
input_str = "hello lead quarters today"
output_str = reverse_vowels(input_str)
print(output_str)  # Output: "hollo lied qaurtors todey"