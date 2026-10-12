def count_small_numbers(text):
    small_numbers = ['1', '2', '3']  # Define small numbers
    counts = {num: 0 for num in small_numbers}  # Initialize count dictionary

    words = text.split()  # Split text into words

    for word in words:
        if word in small_numbers:
            counts[word] += 1  # Increment count for small number

    return counts

# Test the function with the provided text
target_text = "hua hai ajay ko ajay ko ajay ko hello hello everyone welcome to date mini sub hello hello everyone welcome to date mini sub hello hello everyone welcome to date mini sub junior ko darling today's question is junior ko darling today's question is junior ko darling today's question is account small numbers of themselves in this account small numbers of themselves in this account small numbers of themselves in this place in the grave in the winters and need to place in the grave in the winters and need to place in the grave in the winters and need to tell the quantum numbers but exist liquid tell the quantum numbers but exist liquid tell the quantum numbers but exist liquid numbers to write the current element subscribe numbers to write the current element subscribe numbers to write the current element subscribe"
result = count_small_numbers(target_text)
print(result)