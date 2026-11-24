# Declare variables and initialize lists
orList = ListNode(0)
evenList = ListNode(0)
oddHead = orList
evenHead = evenList
isOdd = True

# Traverse the linked list and add nodes to odd or even lists
while head:
    if isOdd:
        oddHead.next = head
        oddHead = oddHead.next
    else:
        evenHead.next = head
        evenHead = evenHead.next
    isOdd = not isOdd
    head = head.next

# Connect odd list to even list and return the head of the odd list
evenHead.next = None
oddHead.next = evenList.next
return orList.next