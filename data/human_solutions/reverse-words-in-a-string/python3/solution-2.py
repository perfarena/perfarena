def reverse_words(s):
    # Split the string by space characters
    words = s.split()
    
    # Reverse the order of words
    words.reverse()
    
    # Create the resulting reversed string
    reversed_string = ""
    for word in words:
        if word:
            reversed_string += word + " "
    
    # Remove the extra space at the end, if any
    if len(reversed_string) > 0:
        reversed_string = reversed_string[:-1]
    
    return reversed_string

# Test the function
input_string = "Sky blue ease"
result = reverse_words(input_string)
print(result)