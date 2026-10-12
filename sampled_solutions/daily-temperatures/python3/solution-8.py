def dailyTemperatures(temperatures):
    result = [0] * len(temperatures)
    stack = []
    
    for index, temp in enumerate(temperatures):
        while stack and temp > stack[-1][1]:
            prev_index, _ = stack.pop()
            result[prev_index] = index - prev_index
        
        stack.append((index, temp))
    
    return result