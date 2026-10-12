func removeDuplicates(_ head: ListNode?) -> ListNode? {
    guard head != nil else {
        return nil // Return nil if the linked list is empty
    }
    
    var current = head
    while current != nil {
        if let nextNode = current?.next, current?.val == nextNode.val {
            current?.next = nextNode.next // Break the link for duplicates
        } else {
            current = current?.next // Move to the next node
        }
    }
    
    return head // Return the modified linked list
}