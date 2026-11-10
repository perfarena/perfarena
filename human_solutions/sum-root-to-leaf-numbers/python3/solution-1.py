def sumNumbers(root):
    def recurse(node, current):
        if not node:
            return 0
        
        current.append(node.val)
        
        if not node.left and not node.right:
            num = int(''.join(map(str, current)))
            res.append(num)
        
        recurse(node.left, current)
        recurse(node.right, current)
        
        current.pop()
    
    res = []
    recurse(root, [])
    
    return sum(res)