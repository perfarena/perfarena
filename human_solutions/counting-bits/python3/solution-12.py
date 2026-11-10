def calculateBinaryRepresentation(input):
    output = [0] * (input + 1)
    
    for i in range(1, input + 1):
        output[i] = output[i >> 1] + (i % 2)
    
    return output

# Test the function
input_number = 5
result = calculateBinaryRepresentation(input_number)
print(result)