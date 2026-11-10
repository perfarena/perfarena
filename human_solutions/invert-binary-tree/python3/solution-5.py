def invert(node):
    if node is None:
        return
    
    # Swap left and right child nodes
    temp = node.left
    node.left = node.right
    node.right = temp
    
    # Recursively invert left and right subtrees
    invert(node.left)
    invert(node.right)
    
    return node