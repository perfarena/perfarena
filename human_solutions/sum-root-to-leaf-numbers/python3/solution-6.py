total = 0

def calculate_sum(node, current):
    global total
    
    if node is None:
        return
    
    if node.left is None and node.right is None:
        total += current * 10 + node.value
        return
    
    calculate_sum(node.left, current * 10 + node.value)
    calculate_sum(node.right, current * 10 + node.value)

# Call the function with the root node to start the calculation
calculate_sum(root, 0)