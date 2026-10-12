def reverse_words(input_str):
    left, right = 0, len(input_str) - 1
    
    # Trim leading spaces
    while left <= right and input_str[left] == ' ':
        left += 1
    
    # Trim trailing spaces
    while left <= right and input_str[right] == ' ':
        right -= 1
    
    # Reverse the trimmed string
    trimmed_str = input_str[left:right+1][::-1]
    
    # Split the reversed string into words
    words = trimmed_str.split()
    
    # Reverse each word in the array
    reversed_words = [word[::-1] for word in words]
    
    # Join the reversed words back together
    return ' '.join(reversed_words)

# Test the function
input_str = "  hello   world  "
output = reverse_words(input_str)
print(output)  # Output: "world hello"