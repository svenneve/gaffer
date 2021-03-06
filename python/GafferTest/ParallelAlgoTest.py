##########################################################################
#
#  Copyright (c) 2018, Image Engine Design Inc. All rights reserved.
#
#  Redistribution and use in source and binary forms, with or without
#  modification, are permitted provided that the following conditions are
#  met:
#
#      * Redistributions of source code must retain the above
#        copyright notice, this list of conditions and the following
#        disclaimer.
#
#      * Redistributions in binary form must reproduce the above
#        copyright notice, this list of conditions and the following
#        disclaimer in the documentation and/or other materials provided with
#        the distribution.
#
#      * Neither the name of John Haddon nor the names of
#        any other contributors to this software may be used to endorse or
#        promote products derived from this software without specific prior
#        written permission.
#
#  THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS
#  IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO,
#  THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR
#  PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR
#  CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL,
#  EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO,
#  PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR
#  PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF
#  LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING
#  NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
#  SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
#
##########################################################################

import thread
import threading

import IECore

import Gaffer
import GafferTest

class ParallelAlgoTest( GafferTest.TestCase ) :

	# Context manager used to run code which is expected to generate a
	# call to `ParallelAlgo.callOnUIThread()`. This emulates the connection
	# to `ParallelAlgo.callOnUIThreadSignal()` that would otherwise be
	# made by GafferUI.EventLoop.
	class ExpectedUIThreadCall( object ) :

		__conditionStack = []
		__conditionStackMutex = threading.Lock()
		__callHandlerRegistered = False

		def __enter__( self ) :

			self.__condition = threading.Condition()
			self.__condition.toCall = None

			with self.__conditionStackMutex :
				self.__conditionStack.append( self.__condition )

			self.__registeredCallHandler = False
			if not self.__class__.__callHandlerRegistered :
				Gaffer.ParallelAlgo.registerUIThreadCallHandler( self.__callOnUIThread )
				self.__class__.__callHandlerRegistered = True
				self.__registeredCallHandler = True

		def __exit__( self, type, value, traceBack ) :

			with self.__condition :

				while self.__condition.toCall is None :
					self.__condition.wait()

				self.__condition.toCall()
				self.__condition.toCall = None

			if self.__registeredCallHandler :
				assert( self.__class__.__callHandlerRegistered == True )
				Gaffer.ParallelAlgo.registerUIThreadCallHandler( None )
				self.__class__.__callHandlerRegistered = False

		@classmethod
		def __callOnUIThread( cls, f ) :

			with cls.__conditionStackMutex :
				condition = cls.__conditionStack.pop()

			with condition :
				assert( condition.toCall is None )
				condition.toCall = f
				condition.notify()

	def testCallOnUIThread( self ) :

		s = Gaffer.ScriptNode()

		def uiThreadFunction() :

			s.setName( "test" )
			s.uiThreadId = thread.get_ident()

		with self.ExpectedUIThreadCall() :

			t = threading.Thread(
				target = lambda : Gaffer.ParallelAlgo.callOnUIThread( uiThreadFunction )
			)
			t.start()
			t.join()

		self.assertEqual( s.getName(), "test" )
		self.assertEqual( s.uiThreadId, thread.get_ident() )

	def testNestedCallOnUIThread( self ) :

		# This is testing our `ExpectedUIThreadCall` utility
		# class more than it's testing `ParallelAlgo`.

		s = Gaffer.ScriptNode()

		def uiThreadFunction1() :

			s.setName( "test" )
			s.uiThreadId1 = thread.get_ident()

		def uiThreadFunction2() :

			s["fileName"].setValue( "test" )
			s.uiThreadId2 = thread.get_ident()

		with self.ExpectedUIThreadCall() :

			t1 = threading.Thread(
				target = lambda : Gaffer.ParallelAlgo.callOnUIThread( uiThreadFunction1 )
			)
			t1.start()

			with self.ExpectedUIThreadCall() :

				t2 = threading.Thread(
					target = lambda : Gaffer.ParallelAlgo.callOnUIThread( uiThreadFunction2 )
				)
				t2.start()

		self.assertEqual( s.getName(), "test" )
		self.assertEqual( s.uiThreadId1, thread.get_ident() )
		self.assertEqual( s["fileName"].getValue(), "test" )
		self.assertEqual( s.uiThreadId2, thread.get_ident() )

		t1.join()
		t2.join()

	def testCallOnBackgroundThread( self ) :

		script = Gaffer.ScriptNode()
		script["n"] = GafferTest.AddNode()

		foregroundContext = Gaffer.Context( script.context() )
		foregroundContext["a"] = "a"

		def f() :

			backgroundContext = Gaffer.Context.current()
			self.assertFalse( backgroundContext.isSame( foregroundContext ) )
			self.assertEqual( backgroundContext, foregroundContext )

			with self.assertRaises( IECore.Cancelled ) :
				while True :
					script["n"]["sum"].getValue()
					# We might expect that `script["n"]["sum"].getValue()`
					# would be guaranteed to throw after cancellation has been
					# requested. But that is not the case if both the hash and the
					# value are already cached, because cancellation is only checked
					# for automatically when a Process is constructed. So we take
					# a belt and braces approach and perform an explicit check here.
					#
					# The alternative would be to move the cancellation check outside
					# of the Process class, so it is performed before the cache lookup.
					# This may be the better approach, but we would need to benchmark
					# it to ensure that performance was not adversely affected. To our
					# knowledge, this "cache hits avoid cancellation" problem has not
					# been responsible for unresponsive cancellation in the wild, because
					# background tasks are typically triggered by `plugDirtiedSignal()`,
					# and the hash cache is cleared when a plug is dirtied.
					IECore.Canceller.check( backgroundContext.canceller() )

		# Explicit cancellation

		with foregroundContext :
			backgroundTask = Gaffer.ParallelAlgo.callOnBackgroundThread( script["n"]["sum"], f )

		backgroundTask.cancel()

		# Implicit cancellation through graph edit

		with foregroundContext :
			backgroundTask = Gaffer.ParallelAlgo.callOnBackgroundThread( script["n"]["sum"], f )

		script["n"]["op1"].setValue( 10 )

		# Cancellation through deletion

		with foregroundContext :
			backgroundTask = Gaffer.ParallelAlgo.callOnBackgroundThread( script["n"]["sum"], f )

		del backgroundTask

if __name__ == "__main__":
	unittest.main()
