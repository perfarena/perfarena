def reverse_words(s):
    # Step 1: Create an array by splitting the input string
    words = s.split()
    
    # Step 2: Initialize a pointer to the last word
    pointer = len(words) - 1
    
    # Step 3: Iterate through the array in reverse order and append words to resulting array
    res = []
    while pointer > -1:
        word = words[pointer]
        res.append(word)
        pointer -= 1
    
    # Step 4: Join the resulting array into a string with words separated by spaces
    reversed_str = ' '.join(res)
    
    # Step 5: Return the final reversed string
    return reversed_str

# Test the function
input_str = "the sky is blue"
output_str = reverse_words(input_str)
print(output_str)  # Output: "blue is sky the"